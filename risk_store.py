import os
import psycopg
from psycopg.rows import dict_row
from decimal import Decimal

DATABASE_URL = os.environ.get("DATABASE_URL")


class RiskError(Exception):
    pass


def conectar():
    if not DATABASE_URL:
        raise RiskError("DATABASE_URL não configurada.")
    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=5
    )


def inicializar_banco():
    sql = """
    CREATE TABLE IF NOT EXISTS risk_cycles (
        id BIGSERIAL PRIMARY KEY,
        account_id BIGINT NOT NULL,
        reference_balance NUMERIC(18, 6) NOT NULL CHECK (reference_balance > 0),
        threshold_percent INTEGER NOT NULL
            CHECK (threshold_percent IN (20, 30)),
        realized_pnl NUMERIC(18, 6) NOT NULL DEFAULT 0,
        locked BOOLEAN NOT NULL DEFAULT FALSE,
        lock_reason TEXT,
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        ended_at TIMESTAMPTZ
    );

    CREATE UNIQUE INDEX IF NOT EXISTS risk_one_active_cycle
    ON risk_cycles ((1))
    WHERE ended_at IS NULL;

    CREATE TABLE IF NOT EXISTS risk_operations (
        id BIGSERIAL PRIMARY KEY,
        cycle_id BIGINT NOT NULL REFERENCES risk_cycles(id),
        command_id TEXT NOT NULL UNIQUE,
        order_id TEXT,
        account_id BIGINT NOT NULL,
        amount NUMERIC(18, 6) NOT NULL CHECK (amount > 0),
        side TEXT NOT NULL,
        option_type TEXT NOT NULL,
        duration INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING'
            CHECK (status IN ('PENDING', 'OPEN', 'REJECTED', 'SETTLED', 'UNKNOWN')),
        realized_pnl NUMERIC(18, 6),
        message TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        settled_at TIMESTAMPTZ
    );

    CREATE UNIQUE INDEX IF NOT EXISTS risk_unique_order_id
    ON risk_operations (order_id)
    WHERE order_id IS NOT NULL;
    """

    with conectar() as conn:
        conn.execute(sql)


def registrar_conta_real(account_id, balance):
    account_id = int(account_id)
    balance = Decimal(str(balance))

    if account_id <= 0 or not balance.is_finite() or balance <= 0:
        raise RiskError("ID ou saldo REAL inválido.")

    with conectar() as conn:
        ciclo = conn.execute(
            """
            SELECT * FROM risk_cycles
            WHERE ended_at IS NULL
            FOR UPDATE
            """
        ).fetchone()

        if ciclo:
            if ciclo["account_id"] != account_id:
                raise RiskError(
                    "Existe um ciclo ativo de outra conta. "
                    "É necessário encerrar o ciclo manualmente."
                )
            return ciclo

        return conn.execute(
            """
            INSERT INTO risk_cycles
                (account_id, reference_balance, threshold_percent)
            VALUES (%s, %s, 20)
            RETURNING *
            """,
            (account_id, balance)
        ).fetchone()


def reservar_ordem(
    account_id,
    command_id,
    amount,
    side,
    option_type,
    duration
):
    account_id = int(account_id)
    amount = Decimal(str(amount))

    if not amount.is_finite() or amount <= 0:
        raise RiskError("Valor da operação inválido.")

    with conectar() as conn:
        ciclo = conn.execute(
            """
            SELECT * FROM risk_cycles
            WHERE ended_at IS NULL
            FOR UPDATE
            """
        ).fetchone()

        if not ciclo:
            raise RiskError("Não existe ciclo de risco REAL ativo.")

        if ciclo["account_id"] != account_id:
            raise RiskError("A conta REAL não corresponde ao ciclo de risco.")

        if ciclo["locked"]:
            raise RiskError("Gestão de risco bloqueada. Desbloqueio manual necessário.")

        limite = ciclo["reference_balance"] * Decimal("0.01")

        if amount > limite:
            raise RiskError(
                f"Ordem excede 1% do saldo de referência. "
                f"Limite: {limite:.2f}"
            )

        return conn.execute(
            """
            INSERT INTO risk_operations
                (cycle_id, command_id, account_id, amount,
                 side, option_type, duration)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                ciclo["id"], command_id, account_id, amount,
                side, option_type, duration
            )
        ).fetchone()


def registrar_abertura(command_id, success, order_id="", message=""):
    with conectar() as conn:
        operacao = conn.execute(
            """
            SELECT * FROM risk_operations
            WHERE command_id = %s
            FOR UPDATE
            """,
            (command_id,)
        ).fetchone()

        if not operacao:
            raise RiskError("Reserva da operação não encontrada.")

        if operacao["status"] != "PENDING":
            raise RiskError("Operação já processada.")

        if success:
            if not order_id:
                raise RiskError("IQ Option não forneceu o ID da ordem.")
            status = "OPEN"
        else:
            status = "REJECTED"

        conn.execute(
            """
            UPDATE risk_operations
            SET status = %s, order_id = %s, message = %s
            WHERE command_id = %s
            """,
            (status, str(order_id) if success else None, message, command_id)
        )


def liquidar_ordem(command_id, order_id, realized_pnl):
    pnl = Decimal(str(realized_pnl))

    if not pnl.is_finite():
        raise RiskError("Resultado financeiro inválido.")

    with conectar() as conn:
        operacao = conn.execute(
            """
            SELECT * FROM risk_operations
            WHERE command_id = %s
            FOR UPDATE
            """,
            (command_id,)
        ).fetchone()

        if not operacao:
            raise RiskError("Operação não encontrada.")

        if operacao["status"] == "SETTLED":
            return {"duplicate": True, "locked": None}

        if operacao["status"] != "OPEN":
            raise RiskError("Operação ainda não está confirmada como aberta.")

        if str(operacao["order_id"]) != str(order_id):
            raise RiskError("ID da ordem não corresponde.")

        ciclo = conn.execute(
            """
            SELECT * FROM risk_cycles
            WHERE id = %s
            FOR UPDATE
            """,
            (operacao["cycle_id"],)
        ).fetchone()

        if not ciclo or ciclo["ended_at"] is not None:
            raise RiskError("Ciclo de risco inválido ou encerrado.")

        conn.execute(
            """
            UPDATE risk_operations
            SET status = 'SETTLED',
                realized_pnl = %s,
                settled_at = NOW()
            WHERE id = %s
            """,
            (pnl, operacao["id"])
        )

        novo_pnl = ciclo["realized_pnl"] + pnl
        limite_perda = (
            ciclo["reference_balance"]
            * Decimal(ciclo["threshold_percent"])
            / Decimal("100")
        )

        bloqueado = novo_pnl <= -limite_perda

        conn.execute(
            """
            UPDATE risk_cycles
            SET realized_pnl = %s,
                locked = CASE WHEN %s THEN TRUE ELSE locked END,
                lock_reason = CASE
                    WHEN %s THEN 'LIMITE DE PERDA ACUMULADA ATINGIDO'
                    ELSE lock_reason
                END
            WHERE id = %s
            """,
            (novo_pnl, bloqueado, bloqueado, ciclo["id"])
        )

        return {
            "duplicate": False,
            "realized_pnl": float(novo_pnl),
            "locked": bool(bloqueado)
        }


def consultar_risco():
    with conectar() as conn:
        ciclo = conn.execute(
            """
            SELECT * FROM risk_cycles
            WHERE ended_at IS NULL
            """
        ).fetchone()

        if not ciclo:
            return None

        return {
            "cycle_id": ciclo["id"],
            "account_id": ciclo["account_id"],
            "reference_balance": float(ciclo["reference_balance"]),
            "threshold_percent": ciclo["threshold_percent"],
            "realized_pnl": float(ciclo["realized_pnl"]),
            "locked": ciclo["locked"],
            "lock_reason": ciclo["lock_reason"]
        }


def listar_ordens_pendentes():
    with conectar() as conn:
        registros = conn.execute(
            """
            SELECT
                o.command_id,
                o.order_id,
                o.account_id,
                o.amount,
                o.side,
                o.option_type,
                o.duration,
                o.status,
                o.created_at
            FROM risk_operations o
            JOIN risk_cycles c ON c.id = o.cycle_id
            WHERE c.ended_at IS NULL
              AND o.status IN ('OPEN', 'UNKNOWN')
            ORDER BY o.created_at ASC
            """
        ).fetchall()

        return [dict(registro) for registro in registros]
