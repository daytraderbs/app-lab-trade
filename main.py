from fastapi.responses import HTMLResponse, JSONResponse
from fastapi import Request
import secrets
import hmac
import time
from fastapi import FastAPI, HTTPException, Security, Depends 
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, StrictInt
from typing import List, Dict
from indicators import calculate_indicators
from risk import calculate_position_size
from strategy import generate_signal
import risk_store
import os
import asyncio
from contextlib import asynccontextmanager
from market_context_service import get_market_context, run_market_context_scheduler
from hybrid_engine import analyze_hybrid
@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(run_market_context_scheduler())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="AI Trade Lab API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app-lab-trade-1.onrender.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# AUTENTICACAO ADMINISTRATIVA
ADMIN_PASSWORD = os.environ.get("PANEL_ADMIN_PASSWORD")
ADMIN_SESSIONS = {}
LOGIN_ATTEMPTS = {}
LOGIN_LIMIT = 5
LOGIN_WINDOW = 15 * 60
SESSION_TTL = 12 * 60 * 60

class AdminLogin(BaseModel):
    password: str

def validar_sessao(request):
    token = request.cookies.get("admin_session")
    expira = ADMIN_SESSIONS.get(token)

    if not token or not expira:
        return False

    if expira < time.time():
        ADMIN_SESSIONS.pop(token, None)
        return False

    return True

@app.post("/api/admin/login")
def admin_login(data: AdminLogin, request: Request):
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=500, detail="Senha administrativa não configurada.")

    ip = request.client.host if request.client else "desconhecido"
    agora = time.time()
    tentativas = LOGIN_ATTEMPTS.get(ip, [])

    tentativas = [t for t in tentativas if agora - t < LOGIN_WINDOW]
    LOGIN_ATTEMPTS[ip] = tentativas

    if len(tentativas) >= LOGIN_LIMIT:
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde 15 minutos.")

    if not hmac.compare_digest(data.password, ADMIN_PASSWORD):
        tentativas.append(agora)
        LOGIN_ATTEMPTS[ip] = tentativas
        raise HTTPException(status_code=401, detail="Senha incorreta.")

    LOGIN_ATTEMPTS.pop(ip, None)
    token = secrets.token_urlsafe(32)
    ADMIN_SESSIONS[token] = time.time() + SESSION_TTL
    response = JSONResponse({"status": "autenticado"})
    response.set_cookie(
        key="admin_session", value=token,
        httponly=True, secure=True, samesite="strict",
        max_age=SESSION_TTL, path="/"
    )
    return response

@app.post("/api/admin/logout")
def admin_logout(request: Request):
    token = request.cookies.get("admin_session")
    ADMIN_SESSIONS.pop(token, None)
    response = JSONResponse({"status": "desconectado"})
    response.delete_cookie("admin_session", path="/")
    return response

@app.get("/")
def redirect_to_docs():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/docs")

# 🔒 CAMADA DE SEGURANÇA: O sistema passa a exigir um Token de Assinante no cabeçalho
API_KEY_NAME = "X-Trader-Token"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

# Token mestre temporário para testes (depois puxaremos dinamicamente do PostgreSQL)
MASTER_SUBSCRIPTION_TOKEN = os.environ.get("MASTER_SUBSCRIPTION_TOKEN")

async def verify_subscriber(api_key: str = Depends(api_key_header)):
    if not MASTER_SUBSCRIPTION_TOKEN:
        raise HTTPException(status_code=500, detail="Token de segurança não configurado no servidor.")
    if not api_key or not secrets.compare_digest(api_key, MASTER_SUBSCRIPTION_TOKEN):
        raise HTTPException(
            status_code=403, 
            detail="Acesso negado. Token de assinatura inválido ou expirado."
        )
    return api_key

class CandleData(BaseModel):
    candles: List[Dict]

class RiskSchema(BaseModel):
    balance: float
    risk_percentage: float
    stop_loss_pips: float

# Rota pública apenas para checar se o servidor está online
@app.get("/api/v1/market-context")
def read_market_context(token: str = Depends(verify_subscriber)):
    return get_market_context()


@app.get("/api")
def read_root():
    return {"status": "AI Trade Lab operacional", "security": "Ativa (Exige X-Trader-Token)"}

# 🔐 Rota protegida: Só calcula os indicadores se o token estiver correto
@app.post("/api/v1/analyze-indicators")
def analyze_market(data: CandleData, token: str = Depends(verify_subscriber)):
    if not data.candles:
        raise HTTPException(status_code=400, detail="A lista de candles não pode estar vazia.")
    
    metrics = calculate_indicators(data.candles)
    if "error" in metrics:
        raise HTTPException(status_code=400, detail=metrics["error"])
        
    decision = generate_signal(metrics)
    return {"pair": "EURUSD", "timeframe": "1m", "indicators": metrics, "decision": decision}

# 🔐 Rota protegida: Só calcula a gestão de risco se for assinante ativo
@app.post("/api/v1/risk-management")
def risk_management(risk: RiskSchema, token: str = Depends(verify_subscriber)):
    result = calculate_position_size(risk.balance, risk.risk_percentage, risk.stop_loss_pips)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result



# CONTROLE SEGURO DA CONTA REAL

class RealAuthorization(BaseModel):
    confirm_real: bool = False

class MonitorCommandResult(BaseModel):
    command_id: str
    account: str
    success: bool
    account_id: StrictInt | None = None
    account_type: StrictInt | None = None
    balance: float | None = None
    message: str = ""


class ManualOrderRequest(BaseModel):
    side: str
    amount: float
    option_type: str
    duration: StrictInt
    confirm_order: bool


class ManualOrderResult(BaseModel):
    command_id: str
    success: bool
    order_id: str = ""
    message: str = ""


class ManualOrderSettlement(BaseModel):
    command_id: str
    order_id: str
    realized_pnl: float

real_authorized = False
real_account_id = None
real_authorization_message = "Operações REAL bloqueadas por segurança."

pending_account_command = None
ACCOUNT_COMMAND_TTL = 60

# FILA DE ORDENS MANUAIS
pending_manual_order = None
MANUAL_ORDER_TTL = 30
last_manual_order_result = None


account_status = {
    "requested": None,
    "confirmed": None,
    "status": "BLOQUEADO",
    "message": "Nenhuma conta REAL autorizada."
}

monitor_timeframe = "1m"


def limpar_comando_expirado():
    global pending_account_command, pending_manual_order
    global real_authorized, real_authorization_message

    if (
        pending_account_command
        and time.time() - pending_account_command["created_at"] > ACCOUNT_COMMAND_TTL
    ):
        pending_account_command = None
        pending_manual_order = None
        real_authorized = False
        account_status["status"] = "EXPIRADO"
        account_status["requested"] = None
        account_status["confirmed"] = None
        account_status["message"] = "Solicitação REAL expirou. Autorização bloqueada."
        real_authorization_message = account_status["message"]


@app.post("/api/v1/trading/real/enable")
def enable_real(data: RealAuthorization, request: Request):
    raise HTTPException(
        status_code=403,
        detail="AI Trade Lab está configurado exclusivamente para análise. Conta REAL indisponível."
    )

@app.post("/api/v1/trading/real/disable")
def disable_real(request: Request):
    global real_authorized, pending_account_command
    global pending_manual_order, real_authorization_message

    if not validar_sessao(request):
        raise HTTPException(status_code=401, detail="Login necessário.")

    real_authorized = False
    pending_account_command = None
    pending_manual_order = None

    account_status.update({
        "requested": None,
        "confirmed": None,
        "status": "BLOQUEADO",
        "message": "REAL bloqueado pelo interruptor PRACTICE."
    })
    real_authorization_message = account_status["message"]

    return {
        "real_authorized": False,
        "message": account_status["message"]
    }



@app.post("/api/v1/trading/manual-order")
def create_manual_order(data: ManualOrderRequest, request: Request):
    raise HTTPException(
        status_code=403,
        detail="Execução de ordens desativada permanentemente neste analisador."
    )

@app.post("/api/v1/monitor/manual-order-result")
def receber_resultado_ordem(
    data: ManualOrderResult,
    token: str = Depends(verify_subscriber)
):
    raise HTTPException(
        status_code=403,
        detail="Recebimento de ordens desativado no modo de análise."
    )

@app.post("/api/v1/monitor/manual-order-settlement")
def receber_liquidacao_ordem(
    data: ManualOrderSettlement,
    token: str = Depends(verify_subscriber)
):
    raise HTTPException(
        status_code=403,
        detail="Liquidação de ordens indisponível no modo de análise."
    )

@app.get("/api/v1/monitor/open-orders")
def recuperar_ordens_abertas(token: str = Depends(verify_subscriber)):
    raise HTTPException(
        status_code=403,
        detail="Consulta de ordens indisponível no modo de análise."
    )

@app.get("/api/v1/trading/manual-order/status")
def status_ordem_manual(command_id: str, request: Request):
    raise HTTPException(
        status_code=403,
        detail="Status de ordens indisponível no modo de análise."
    )

@app.get("/api/v1/monitor/commands")
def get_monitor_commands(token: str = Depends(verify_subscriber)):
    return {
        "timeframe": monitor_timeframe,
        "account_command": None,
        "manual_order": None,
        "real_authorized": False,
        "account_status": {
            "requested": None,
            "confirmed": None,
            "status": "BLOQUEADO",
            "message": "Modo exclusivo de análise. Nenhuma ordem pode ser executada."
        }
    }

@app.post("/api/v1/monitor/command-result")
def receive_monitor_command_result(
    data: MonitorCommandResult,
    token: str = Depends(verify_subscriber)
):
    raise HTTPException(
        status_code=403,
        detail="Comandos de conta desativados no modo de análise."
    )

@app.post("/api/v1/monitor/timeframe")
def set_monitor_timeframe(data: Dict, request: Request):
    global monitor_timeframe

    if not validar_sessao(request):
        raise HTTPException(status_code=401, detail="Login necessário.")

    timeframe = data.get("timeframe")

    if timeframe not in ("1m", "15m"):
        raise HTTPException(status_code=400, detail="Timeframe inválido.")

    monitor_timeframe = timeframe
    return {"status": "ok", "timeframe": monitor_timeframe}


@app.post("/api/v1/monitor/account-state")
def receive_account_state(data: Dict, token: str = Depends(verify_subscriber)):
    raise HTTPException(status_code=410, detail="Sincronizacao livre desativada.")


# Estados independentes recebidos do monitor
def novo_monitor_state():
    return {
        "updated_at": None,
        "latest": None,
        "history": [],
        "candles": [],
        "stats": {}
    }

monitor_states = {
    "1m": novo_monitor_state(),
    "15m": novo_monitor_state()
}

@app.post("/api/v1/monitor/update")
def update_monitor(data: Dict, token: str = Depends(verify_subscriber)):
    from datetime import datetime, timezone

    timeframe = data.get("timeframe")
    if timeframe not in ("1m", "15m"):
        raise HTTPException(status_code=400, detail="Timeframe inválido.")

    if not isinstance(data.get("latest"), dict):
        raise HTTPException(status_code=400, detail="Sinal atual inválido.")

    if not isinstance(data.get("history", []), list):
        raise HTTPException(status_code=400, detail="Histórico inválido.")

    if not isinstance(data.get("candles", []), list):
        raise HTTPException(status_code=400, detail="Candles inválidos.")

    if not isinstance(data.get("stats", {}), dict):
        raise HTTPException(status_code=400, detail="Estatísticas inválidas.")

    estado = monitor_states[timeframe]
    estado["latest"] = data["latest"]
    estado["history"] = data.get("history", [])[-300:]
    estado["candles"] = data.get("candles", [])[-205:]
    estado["stats"] = data.get("stats", {})
    estado["updated_at"] = datetime.now(timezone.utc).isoformat()

    return {
        "status": "recebido",
        "timeframe": timeframe,
        "updated_at": estado["updated_at"]
    }


@app.get("/api/v1/monitor/state")
def get_monitor_state(token: str = Depends(verify_subscriber)):
    return {**monitor_states[monitor_timeframe], "timeframe": monitor_timeframe}




@app.get("/api/v1/hybrid-analysis")
def read_hybrid_analysis(token: str = Depends(verify_subscriber)):
    timeframe = monitor_timeframe
    state = monitor_states[timeframe]

    result = analyze_hybrid(
        get_market_context(),
        state.get("candles", []),
        timeframe
    )

    result["monitor_updated_at"] = state.get("updated_at")
    return result


@app.get("/api/admin/hybrid-analysis")
def admin_hybrid_analysis(request: Request):
    if not validar_sessao(request):
        raise HTTPException(status_code=401, detail="Login administrativo necessário.")

    timeframe = monitor_timeframe
    state = monitor_states[timeframe]

    result = analyze_hybrid(
        get_market_context(),
        state.get("candles", []),
        timeframe
    )

    result["monitor_updated_at"] = state.get("updated_at")
    return result


@app.get("/api/v1/monitor/public-state")
def public_monitor_state(request: Request):
    if not validar_sessao(request):
        raise HTTPException(status_code=401, detail="Login administrativo necessário.")
    estado = {**monitor_states[monitor_timeframe], "timeframe": monitor_timeframe}
    estado["trading"] = {
        "real_authorized": real_authorized,
        "message": account_status["message"],
        "account_status": account_status.copy()
    }
    return estado


@app.get("/painel", response_class=HTMLResponse)
def painel(request: Request):
    if not validar_sessao(request):
        return HTMLResponse("""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Login • AI Trade Lab</title>
<style>
body{background:#080f1c;color:#e8eef8;font-family:Arial;display:grid;place-items:center;min-height:90vh}
.card{background:#17263a;padding:28px;border-radius:15px;width:85%;max-width:350px}
input,button{box-sizing:border-box;width:100%;padding:13px;margin-top:12px;border-radius:8px}
button{background:#20c997;border:0;font-weight:bold}
#erro{color:#ff7185}
</style>
</head>
<body>
<div class="card">
<h2>AI Trade Lab</h2>
<p>Acesso administrativo</p>
<form id="login">
<input id="senha" type="password" placeholder="Senha administrativa" required>
<button type="submit">ENTRAR</button>
</form>
<p id="erro"></p>
</div>
<script>
document.getElementById("login").addEventListener("submit",async e=>{
 e.preventDefault();
 const erro=document.getElementById("erro");
 try{
  const r=await fetch("/api/admin/login",{
   method:"POST",
   headers:{"Content-Type":"application/json"},
   body:JSON.stringify({password:document.getElementById("senha").value})
  });
  if(!r.ok) throw new Error("Senha incorreta ou erro de autenticação.");
  location.reload();
 }catch(err){erro.textContent=err.message;}
});
</script>
</body>
</html>
""", status_code=401)
    return """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Trade Lab</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#080f1c;color:#e8eef8;font-family:Arial,sans-serif;padding:22px}
.container{max-width:1200px;margin:auto}
h1{color:#48e0ad;font-size:30px;margin-bottom:5px}
h3{color:#dce7f5;margin-top:0}
p.menor,.menor{color:#91a2b9}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:14px}
.card{background:linear-gradient(145deg,#17263a,#101c2d);border:1px solid #263b53;padding:20px;border-radius:15px;margin:14px 0;box-shadow:0 8px 24px #0003}
.valor{font-size:27px;font-weight:700;margin-top:12px;color:#fff}
#sinal{color:#48e0ad}
#conexao{color:#48e0ad;font-size:21px}
table{width:100%;border-collapse:collapse;min-width:520px}
td,th{padding:13px 10px;border-bottom:1px solid #293b50;text-align:left}
th{color:#48e0ad;font-size:12px;text-transform:uppercase}
td{font-size:14px}
canvas{display:block;max-width:100%}
#atualizacao{font-size:12px;text-align:center;margin-top:20px}
@media(max-width:600px){
 body{padding:13px}
 h1{font-size:25px}
 .card{padding:16px}
 .valor{font-size:23px}
}
</style>
</head>
<body>
<div class="container">
<h1>AI Trade Lab</h1>
<p class="menor" id="tituloMonitor">Monitor de mercado • EUR/USD • M1</p>
<div class="card">
<h3>Timeframe do monitor</h3>
<select id="timeframeSelect" onchange="mudarTimeframe(this.value)"
style="padding:13px;width:100%;border-radius:8px;background:#17263a;color:white;border:1px solid #48e0ad">
<option value="1m">M1 — 1 minuto</option>
<option value="15m">M15 — 15 minutos</option>
</select>
<div class="menor" id="timeframeStatus">Monitor único • Alternância sem reinicialização.</div>
</div>
<button onclick="sairPainel()" style="float:right;background:#b83248;color:white;border:0;padding:10px 18px;border-radius:8px;cursor:pointer">SAIR</button>
<div class="card">
<h3>Controle de segurança</h3>
<button id="practiceBtn" onclick="alternarReal(false)">PRACTICE — BLOQUEAR REAL</button>
<button id="realBtn" onclick="alternarReal(true)">REAL — AUTORIZAR</button>
<div id="contaSelecionada" class="menor">Autorização REAL: BLOQUEADA</div>
<div id="avisoConta" class="menor">Operações reais bloqueadas por segurança.</div>
</div>



<div class="grid">
<div class="card">SINAL ATUAL<div class="valor" id="sinal">Aguardando dados</div></div>
<div class="card">TAXA DE ACERTO<div class="valor" id="taxa">--</div></div>
<div class="card">AVALIADAS<div class="valor" id="avaliadas">0</div></div>
<div class="card">ACERTOS / ERROS<div class="valor" id="resultado">0 / 0</div></div>
<div class="card">CONEXÃO DO ROBÔ<div class="valor" id="conexao">AGUARDANDO</div></div>
</div>
<div class="grid">
<div class="card">RSI 14<div class="valor" id="rsi">--</div></div>
<div class="card">MACD<div class="valor" id="macd">--</div></div>
<div class="card">ATR 14<div class="valor" id="atr">--</div></div>
</div>
<div class="card">
<h3>Último sinal</h3>
<div id="detalhe" class="menor">Aguardando sincronização do monitor.</div>
</div>
<div class="card" style="margin-top:15px">
<h3>Inteligência híbrida EUR/USD</h3>
<div class="menor" id="hybridStatus">Aguardando análise...</div>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-top:12px">
 <div class="card"><strong>Contexto econômico</strong><div id="hybridContext">--</div></div>
 <div class="card"><strong>Ação dos preços</strong><div id="hybridPrice">--</div></div>
 <div class="card"><strong>Regime de mercado</strong><div id="hybridRegime">--</div></div>
 <div class="card"><strong>Decisão</strong><div id="hybridDecision">AGUARDAR</div></div>
</div>
<h4>Métricas econômicas</h4>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px">
 <div>Juros ECB: <span id="hmEcb">--</span></div>
 <div>Juros FED: <span id="hmFed">--</span></div>
 <div>Inflação zona do euro: <span id="hmEuroInflation">--</span></div>
 <div>Desemprego zona do euro: <span id="hmEuroUnemployment">--</span></div>
 <div>PIB zona do euro: <span id="hmEuroGdp">--</span></div>
 <div>Inflação EUA: <span id="hmUsInflation">--</span></div>
 <div>Desemprego EUA: <span id="hmUsUnemployment">--</span></div>
 <div>PIB EUA: <span id="hmUsGdp">--</span></div>
</div>
<div class="menor" id="hybridDetails" style="margin-top:12px">Análise ainda não disponível.</div>
</div>
<div class="card">
<h3 id="tituloGrafico">Gráfico EUR/USD · M1</h3>
<canvas id="grafico" height="180" style="width:100%"></canvas>
</div>
<div class="card">
<h3>Histórico recente</h3>
<div style="overflow-x:auto">
<table><thead><tr><th>Horário</th><th>Sinal</th><th>Score</th><th>Status</th><th>Resultado</th></tr></thead>
<tbody id="historico"><tr><td colspan="5">Sem dados.</td></tr></tbody></table>
</div>
</div>
<p class="menor" id="atualizacao">Aguardando atualização...</p>
</div>
<script>
async function sairPainel(){
    await fetch("/api/admin/logout", {method:"POST"});
    location.reload();
}

function horarioBR(data){
 if(!data) return "--";
 const d=new Date(data);
 return isNaN(d)?"--":new Intl.DateTimeFormat("pt-BR",{
  timeZone:"America/Sao_Paulo",
  dateStyle:"short",
  timeStyle:"medium"
 }).format(d);
}
function desenharGrafico(candles){
 const c=document.getElementById("grafico");
 const ctx=c.getContext("2d");
 const w=c.clientWidth;
 const h=280;
 const ratio=window.devicePixelRatio||1;
 c.width=w*ratio;
 c.height=h*ratio;
 ctx.scale(ratio,ratio);
 ctx.clearRect(0,0,w,h);

 if(!candles||candles.length<2){
  ctx.fillStyle="#91a2b9";
  ctx.fillText("Aguardando candles da IQ Option...",12,30);
  return;
 }

 function calcularEMA(periodo){
  const alpha=2/(periodo+1);
  let ema=Number(candles[0].close);
  return candles.map((v,i)=>{
   const preco=Number(v.close);
   if(i===0)return ema;
   ema=preco*alpha+ema*(1-alpha);
   return ema;
  });
 }

 const e9=calcularEMA(9);
 const e21=calcularEMA(21);
 const e200=calcularEMA(200);
 const inicio=Math.max(0,candles.length-50);
 const dados=candles.slice(inicio);
 const valores=dados.flatMap(v=>[
  Number(v.high),Number(v.low)
 ]);
 [e9,e21,e200].forEach(a=>valores.push(...a.slice(inicio)));
 const min=Math.min(...valores);
 const max=Math.max(...valores);
 const margem=(max-min)*0.10||0.0001;
 const topo=min-margem;
 const fundo=max+margem;
 const esquerda=12, direita=65, acima=15, abaixo=22;
 const largura=w-esquerda-direita;
 const altura=h-acima-abaixo;
 const y=v=>acima+(fundo-v)/(fundo-topo)*altura;

 ctx.font="11px Arial";
 ctx.strokeStyle="#293b50";
 ctx.fillStyle="#91a2b9";
 for(let i=0;i<=4;i++){
  const valor=fundo-(fundo-topo)*i/4;
  const yy=acima+altura*i/4;
  ctx.beginPath();
  ctx.moveTo(esquerda,yy);
  ctx.lineTo(w-direita,yy);
  ctx.stroke();
  ctx.fillText(valor.toFixed(5),w-direita+5,yy+4);
 }

 const passo=largura/dados.length;
 const corpo=Math.max(2,Math.min(9,passo*0.65));

 dados.forEach((v,j)=>{
  const x=esquerda+passo*(j+0.5);
  const abertura=Number(v.open);
  const fechamento=Number(v.close);
  const alta=fechamento>=abertura;
  const cor=alta?"#20c997":"#ff5c70";
  ctx.strokeStyle=cor;
  ctx.fillStyle=cor;
  ctx.lineWidth=1;
  ctx.beginPath();
  ctx.moveTo(x,y(Number(v.high)));
  ctx.lineTo(x,y(Number(v.low)));
  ctx.stroke();
  const y1=y(Math.max(abertura,fechamento));
  const y2=y(Math.min(abertura,fechamento));
  ctx.fillRect(x-corpo/2,y1,corpo,Math.max(1,y2-y1));
 });

 function linhaEMA(valoresEMA,cor){
  ctx.beginPath();
  ctx.strokeStyle=cor;
  ctx.lineWidth=1.7;
  valoresEMA.slice(inicio).forEach((v,j)=>{
   const x=esquerda+passo*(j+0.5);
   if(j===0)ctx.moveTo(x,y(v));
   else ctx.lineTo(x,y(v));
  });
  ctx.stroke();
 }
 linhaEMA(e9,"#f5c451");
 linhaEMA(e21,"#45a9ff");
 linhaEMA(e200,"#c58aff");

 ctx.fillStyle="#f5c451";
 ctx.fillText("EMA 9",12,h-5);
 ctx.fillStyle="#45a9ff";
 ctx.fillText("EMA 21",75,h-5);
 ctx.fillStyle="#c58aff";
 ctx.fillText("EMA 200",145,h-5);
}


let realAutorizado = false;
let solicitandoConta = false;

function atualizarEstadoConta(trading) {
 const label = document.getElementById("contaSelecionada");
 const aviso = document.getElementById("avisoConta");
 const practice = document.getElementById("practiceBtn");
 const real = document.getElementById("realBtn");

 realAutorizado = trading?.real_authorized === true;



 label.textContent = "Autorização REAL: " + (realAutorizado ? "ATIVA" : "BLOQUEADA");
 practice.style.opacity = realAutorizado ? "0.55" : "1";
 real.style.opacity = realAutorizado ? "1" : "0.55";
 aviso.textContent = trading?.message || "REAL bloqueado por segurança.";
}

async function alternarReal(ativar) {
 if (solicitandoConta) return;

 if (ativar && !confirm("Confirma habilitar a autorização para operações na conta REAL? Cada ordem ainda exigirá confirmação individual.")) return;

 solicitandoConta = true;
 const practice = document.getElementById("practiceBtn");
 const real = document.getElementById("realBtn");
 practice.disabled = true;
 real.disabled = true;

 try {
  const resposta = await fetch(
   ativar ? "/api/v1/trading/real/enable" : "/api/v1/trading/real/disable",
   {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: ativar ? JSON.stringify({confirm_real: true}) : "{}"
   }
  );

  const dados = await resposta.json();
  if (!resposta.ok) throw new Error(dados.detail || "Falha ao alterar autorização.");

  atualizarEstadoConta(dados);
 } catch (erro) {
  document.getElementById("avisoConta").textContent = erro.message;
 } finally {
  solicitandoConta = false;
  practice.disabled = false;
  real.disabled = false;
 }
}

let timeframeSelecionado = "1m";
let alterandoTimeframe = false;

async function mudarTimeframe(timeframe){
 if(alterandoTimeframe) return;
 alterandoTimeframe = true;
 const seletor = document.getElementById("timeframeSelect");
 const status = document.getElementById("timeframeStatus");
 seletor.disabled = true;

 try{
  const r = await fetch("/api/v1/monitor/timeframe", {
   method:"POST",
   headers:{"Content-Type":"application/json"},
   body:JSON.stringify({timeframe})
  });
  const d = await r.json();
  if(!r.ok) throw new Error(d.detail || "Falha ao alterar timeframe.");

  timeframeSelecionado = d.timeframe;
  status.textContent = "Timeframe ativo: " + (timeframe === "1m" ? "M1" : "M15");
  await atualizar();
 }catch(e){
  seletor.value = timeframeSelecionado;
  status.textContent = "Erro: " + e.message;
 }finally{
  seletor.disabled = false;
  alterandoTimeframe = false;
 }
}

async function atualizar(){
 try{
  const r=await fetch('/api/v1/monitor/public-state');
  if(!r.ok) throw new Error('HTTP '+r.status);
  const d=await r.json();
  atualizarEstadoConta(d.trading);
  const tf=d.timeframe||"1m";
  timeframeSelecionado=tf;
  document.getElementById("timeframeSelect").value=tf;
  const nomeTF=tf==="15m"?"M15":"M1";
  document.getElementById("tituloMonitor").textContent="Monitor de mercado • EUR/USD • "+nomeTF;
  document.getElementById("tituloGrafico").textContent="Gráfico EUR/USD · "+nomeTF;
  const x=d.latest||{},s=d.stats||{};
  const ultima=d.updated_at?new Date(d.updated_at).getTime():0;
  const online=ultima>0&&(Date.now()-ultima)<1200000;
  document.getElementById("conexao").textContent=online?"ONLINE":"SEM COMUNICAÇÃO";
  document.getElementById('sinal').textContent=x.sinal||'AGUARDAR';
  document.getElementById('taxa').textContent=(s.taxa_acerto??0)+'%';
  document.getElementById('avaliadas').textContent=s.avaliadas??0;
  document.getElementById('resultado').textContent=(s.acertos??0)+' / '+(s.erros??0);
  document.getElementById("rsi").textContent=x.rsi_14!=null?Number(x.rsi_14).toFixed(2):"--";
  document.getElementById("macd").textContent=x.macd_histogram!=null?Number(x.macd_histogram).toFixed(6):"--";
  document.getElementById("atr").textContent=x.atr_14!=null?Number(x.atr_14).toFixed(6):"--";
  document.getElementById('detalhe').textContent=x.data_hora
   ? 'Horário de Brasília: '+horarioBR(x.data_hora)+' | Preço: '+(x.preco??'--')+' | Score: '+(x.score??'--')
   : 'Aguardando sincronização do monitor.';
  const linhas=(d.history||[]).slice().reverse().slice(0,30);
  desenharGrafico(d.candles||[]);
  document.getElementById('historico').innerHTML=linhas.length?linhas.map(v=>
   '<tr><td>'+horarioBR(v.data_hora)+'</td><td>'+(v.sinal||'--')+'</td><td>'+(v.score??'--')+'</td><td>'+(v.status||'--')+'</td><td>'+(v.resultado||'--')+'</td></tr>'
  ).join(''):'<tr><td colspan="5">Sem dados.</td></tr>';
  document.getElementById('atualizacao').textContent='Última sincronização: '+horarioBR(d.updated_at);
 }catch(e){
  document.getElementById('atualizacao').textContent='Erro ao atualizar: '+e.message;
 }
}

function hybridSet(id, value){
 const el=document.getElementById(id);
 if(el) el.textContent=value;
}

function hybridMetric(metric){
 if(!metric) return "--";
 const value=metric.value;
 const formatted=value==null?"--":
  (typeof value==="number"?value.toFixed(2):String(value))+
  (metric.units?" "+metric.units:"");
 const date=metric.observation_date||metric.effective_date||metric.published_at;
 return formatted+(date?" · "+date:"");
}

let hybridBusy=false;

async function atualizarAnaliseHibrida(){
 if(hybridBusy) return;
 hybridBusy=true;
 try{
  const r=await fetch("/api/admin/hybrid-analysis");
  if(!r.ok) throw new Error("HTTP "+r.status);
  const d=await r.json();
  const ctx=d.market_context||{};
  const metrics=ctx.metrics||{};
  const price=d.price_analysis||{};
  const action=price.recent_price_action||{};
  const candle=price.latest_candle||{};
  const regime=price.market_regime||{};

  hybridSet("hybridStatus",
   "Status: "+(d.status||"DESCONHECIDO")+
   " · Timeframe: "+(d.timeframe||"--")+
   " · Velas fechadas: "+(d.closed_candles??price.candles_analyzed??0));

  hybridSet("hybridContext",
   (ctx.status||"INDISPONÍVEL")+" · Métricas válidas: "+
   (ctx.valid_metrics??0)+"/"+(ctx.total_metrics??8));

  hybridSet("hybridPrice",price.status==="OK"?
   (action.direction||"INDEFINIDA")+" · Alta: "+(action.bullish_candles??0)+
   " · Baixa: "+(action.bearish_candles??0)+
   " · Neutras: "+(action.neutral_candles??0):
   "Dados insuficientes");

  hybridSet("hybridRegime",regime.regime||"INDEFINIDO");
  hybridSet("hybridDecision",d.decision||"AGUARDAR");

  hybridSet("hmEcb",hybridMetric(metrics.ecb_rate));
  hybridSet("hmFed",hybridMetric(metrics.fed_rate));
  hybridSet("hmEuroInflation",hybridMetric(metrics.eurozone_inflation));
  hybridSet("hmEuroUnemployment",hybridMetric(metrics.eurozone_employment));
  hybridSet("hmEuroGdp",hybridMetric(metrics.eurozone_gdp));
  hybridSet("hmUsInflation",hybridMetric(metrics.united_states_inflation));
  hybridSet("hmUsUnemployment",hybridMetric(metrics.united_states_employment));
  hybridSet("hmUsGdp",hybridMetric(metrics.united_states_gdp));

  const variation=Number(action.variation_10_candles);
  const variationText=Number.isFinite(variation)?
   "Variação das 10 velas: "+(variation*100).toFixed(4)+"%":"";
  hybridSet("hybridDetails",
   (price.status==="OK"?
    "Última vela: "+(candle.type||"--")+
    " · "+variationText+
    " · Regime: "+(regime.reason||"Sem justificativa disponível."):
    (d.reason||"Histórico insuficiente para análise de preços."))+
   " · Notícias: "+(ctx.news_status||"INDISPONÍVEIS")+
   " · Execução automática: "+(d.execution===false?"DESATIVADA":"NÃO AUTORIZADA"));
 }catch(e){
  hybridSet("hybridStatus","Falha na análise híbrida: "+e.message);
 }finally{
  hybridBusy=false;
 }
}

atualizar();
atualizarAnaliseHibrida();
setInterval(atualizar,2000);
setInterval(atualizarAnaliseHibrida,10000);
</script>
</body>
</html>
"""



if __name__== "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=80, reload=False)
