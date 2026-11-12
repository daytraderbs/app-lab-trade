from fastapi.responses import HTMLResponse, JSONResponse
from fastapi import Request
import secrets
import hmac
import time
from fastapi import FastAPI, HTTPException, Security, Depends 
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
from typing import List, Dict
from indicators import calculate_indicators
from risk import calculate_position_size
from strategy import generate_signal
import os
app = FastAPI(title="AI Trade Lab API")
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
    if not api_key or api_key != MASTER_SUBSCRIPTION_TOKEN:
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



# CONTROLE SEGURO DE SELECAO DE CONTA
class AccountSelection(BaseModel):
    account: str
    confirm_real: bool = False

class AccountCommandResult(BaseModel):
    command_id: str
    success: bool
    account: str
    message: str = ""

pending_account_command = None
account_status = {
    "requested": "PRACTICE",
    "confirmed": None,
    "status": "AGUARDANDO_MONITOR",
    "message": "Aguardando confirmação da IQ Option."
}

def limpar_comando_expirado():
    global pending_account_command

    if pending_account_command is None:
        return

    idade = time.time() - pending_account_command["created_at"]

    if idade > 60:
        conta = pending_account_command["account"]
        pending_account_command = None
        account_status.update({
            "status": "EXPIRADO",
            "message": f"Solicitação de {conta} expirou. Tente novamente."
        })


@app.post("/api/v1/account/select")
def select_account(data: AccountSelection, request: Request):
    global pending_account_command

    if not validar_sessao(request):
        raise HTTPException(status_code=401, detail="Login necessário.")

    if data.account not in ("PRACTICE", "REAL"):
        raise HTTPException(status_code=400, detail="Conta inválida.")

    if data.account == "REAL" and data.confirm_real is not True:
        raise HTTPException(status_code=403, detail="Confirmação explícita de REAL necessária.")

    limpar_comando_expirado()

    if pending_account_command is not None:
        raise HTTPException(status_code=409, detail="Já existe uma troca pendente.")

    pending_account_command = {
        "command_id": secrets.token_urlsafe(24),
        "account": data.account,
        "created_at": time.time()
    }

    account_status.update({
        "requested": data.account,
        "status": "PENDENTE",
        "message": "Aguardando confirmação da IQ Option."
    })

    return {"status": "PENDENTE", "command_id": pending_account_command["command_id"]}


@app.get("/api/v1/monitor/commands")
def get_monitor_commands(token: str = Depends(verify_subscriber)):
    limpar_comando_expirado()
    return {"command": pending_account_command}


@app.post("/api/v1/monitor/command-result")
def receive_command_result(data: AccountCommandResult, token: str = Depends(verify_subscriber)):
    global pending_account_command

    if not pending_account_command or data.command_id != pending_account_command["command_id"]:
        raise HTTPException(status_code=409, detail="Comando inexistente ou já processado.")

    if data.account != pending_account_command["account"]:
        raise HTTPException(status_code=400, detail="Conta retornada não corresponde à solicitada.")

    account_status.update({
        "requested": data.account,
        "confirmed": data.account if data.success else account_status["confirmed"],
        "status": "CONFIRMADA" if data.success else "FALHA",
        "message": data.message
    })

    pending_account_command = None

    return {"status": "recebido"}



@app.post("/api/v1/monitor/account-state")
def receive_account_state(data: Dict, token: str = Depends(verify_subscriber)):
    conta = data.get("account")
    if conta != "PRACTICE":
        raise HTTPException(
            status_code=403,
            detail="Sincronização inicial permitida somente em PRACTICE."
        )

    account_status.update({
        "requested": conta,
        "confirmed": conta,
        "status": "CONFIRMADA",
        "message": f"Conta {conta} confirmada pela IQ Option."
    })
    return {"status": "recebido"}


# Estado recebido do monitor PRACTICE
monitor_state = {
    "updated_at": None,
    "latest": None,
    "history": [],
    "candles": [],
    "stats": {}
}

@app.post("/api/v1/monitor/update")
def update_monitor(data: Dict, token: str = Depends(verify_subscriber)):
    from datetime import datetime, timezone

    if not isinstance(data.get("latest"), dict):
        raise HTTPException(status_code=400, detail="Sinal atual inválido.")

    if not isinstance(data.get("history", []), list):
        raise HTTPException(status_code=400, detail="Histórico inválido.")

    if not isinstance(data.get("stats", {}), dict):
        raise HTTPException(status_code=400, detail="Estatísticas inválidas.")

    monitor_state["latest"] = data["latest"]
    monitor_state["history"] = data.get("history", [])[-300:]
    monitor_state["candles"] = data.get("candles", [])[-205:]
    monitor_state["stats"] = data.get("stats", {})
    monitor_state["updated_at"] = datetime.now(timezone.utc).isoformat()

    return {"status": "recebido", "updated_at": monitor_state["updated_at"]}


@app.get("/api/v1/monitor/state")
def get_monitor_state(token: str = Depends(verify_subscriber)):
    return monitor_state.copy()



@app.get("/api/v1/monitor/public-state")
def public_monitor_state(request: Request):
    if not validar_sessao(request):
        raise HTTPException(status_code=401, detail="Login administrativo necessário.")
    estado = monitor_state.copy()
    estado["account"] = account_status.copy()
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
<p class="menor">Monitor de mercado • EUR/USD • M15</p>
<button onclick="sairPainel()" style="float:right;background:#b83248;color:white;border:0;padding:10px 18px;border-radius:8px;cursor:pointer">SAIR</button>
<div class="card">
<h3>Seleção de conta</h3>
<button id="practiceBtn" onclick="selecionarConta('PRACTICE')">PRACTICE</button>
<button id="realBtn" onclick="selecionarConta('REAL')">REAL</button>
<div id="contaSelecionada" class="menor">Conta selecionada: PRACTICE</div>
<div id="avisoConta" class="menor">Monitor conectado em PRACTICE. Selecionar REAL não executa operações.</div>
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
<div class="card">
<h3>Gráfico EUR/USD · M15</h3>
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


let contaSelecionada = null;
let solicitandoConta = false;

function atualizarEstadoConta(conta) {
 const label = document.getElementById("contaSelecionada");
 const aviso = document.getElementById("avisoConta");
 const practice = document.getElementById("practiceBtn");
 const real = document.getElementById("realBtn");

 const confirmada = conta && conta.confirmed;
 contaSelecionada = confirmada || null;

 label.textContent = confirmada
  ? "Conta confirmada: " + confirmada
  : "Conta ainda não confirmada";

 practice.style.opacity = confirmada === "PRACTICE" ? "1" : "0.55";
 real.style.opacity = confirmada === "REAL" ? "1" : "0.55";
 aviso.textContent = conta?.message || "Aguardando monitor da IQ Option.";

 if (conta?.status === "PENDENTE") {
  aviso.textContent = "Aguardando confirmação da IQ Option.";
 }
}

async function selecionarConta(conta) {
 if (!["PRACTICE", "REAL"].includes(conta) || solicitandoConta) return;

 if (conta === "REAL" &&
     !confirm("Confirma a troca para a conta REAL? Nenhuma ordem será enviada.")) return;

 solicitandoConta = true;
 const practice = document.getElementById("practiceBtn");
 const real = document.getElementById("realBtn");
 practice.disabled = true;
 real.disabled = true;

 document.getElementById("avisoConta").textContent =
  "Enviando solicitação ao monitor...";

 try {
  const resposta = await fetch("/api/v1/account/select", {
   method: "POST",
   headers: {"Content-Type": "application/json"},
   body: JSON.stringify({
    account: conta,
    confirm_real: conta === "REAL"
   })
  });

  const dados = await resposta.json();
  if (!resposta.ok) throw new Error(dados.detail || "Falha na solicitação.");

  document.getElementById("avisoConta").textContent =
   "Solicitação enviada. Aguardando IQ Option.";

 } catch (erro) {
  document.getElementById("avisoConta").textContent = erro.message;
 } finally {
  solicitandoConta = false;
  practice.disabled = false;
  real.disabled = false;
 }
}

async function atualizar(){
 try{
  const r=await fetch('/api/v1/monitor/public-state');
  if(!r.ok) throw new Error('HTTP '+r.status);
  const d=await r.json();
  atualizarEstadoConta(d.account);
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
atualizar();
setInterval(atualizar,2000);
</script>
</body>
</html>
"""



if __name__== "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=80, reload=False)
