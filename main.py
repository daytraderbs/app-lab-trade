from fastapi.responses import HTMLResponse
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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
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
    return {"pair": "EURUSD", "timeframe": "15m", "indicators": metrics, "decision": decision}

# 🔐 Rota protegida: Só calcula a gestão de risco se for assinante ativo
@app.post("/api/v1/risk-management")
def risk_management(risk: RiskSchema, token: str = Depends(verify_subscriber)):
    result = calculate_position_size(risk.balance, risk.risk_percentage, risk.stop_loss_pips)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# Estado recebido do monitor PRACTICE
monitor_state = {
    "updated_at": None,
    "latest": None,
    "history": [],
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
    monitor_state["stats"] = data.get("stats", {})
    monitor_state["updated_at"] = datetime.now(timezone.utc).isoformat()

    return {"status": "recebido", "updated_at": monitor_state["updated_at"]}


@app.get("/api/v1/monitor/state")
def get_monitor_state(token: str = Depends(verify_subscriber)):
    return monitor_state.copy()



@app.get("/api/v1/monitor/public-state")
def public_monitor_state():
    return monitor_state.copy()


@app.get("/painel", response_class=HTMLResponse)
def painel():
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
<p class="menor">Monitor de mercado • EUR/USD • M15 • Conta PRACTICE</p>
<div class="grid">
<div class="card">SINAL ATUAL<div class="valor" id="sinal">Aguardando dados</div></div>
<div class="card">TAXA DE ACERTO<div class="valor" id="taxa">--</div></div>
<div class="card">AVALIADAS<div class="valor" id="avaliadas">0</div></div>
<div class="card">ACERTOS / ERROS<div class="valor" id="resultado">0 / 0</div></div>
<div class="card">CONEXÃO DO ROBÔ<div class="valor" id="conexao">AGUARDANDO</div></div>
</div>
<div class="card">
<h3>Último sinal</h3>
<div id="detalhe" class="menor">Aguardando sincronização do monitor.</div>
</div>
<div class="card">
<h3>Evolução dos sinais</h3>
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
function horarioBR(data){
 if(!data) return "--";
 const d=new Date(data);
 return isNaN(d)?"--":new Intl.DateTimeFormat("pt-BR",{
  timeZone:"America/Sao_Paulo",
  dateStyle:"short",
  timeStyle:"medium"
 }).format(d);
}
function desenharGrafico(linhas){
 const c=document.getElementById("grafico");
 const ctx=c.getContext("2d");
 const w=c.clientWidth;
 const h=180;
 c.width=w*devicePixelRatio;
 c.height=h*devicePixelRatio;
 ctx.scale(devicePixelRatio,devicePixelRatio);
 ctx.clearRect(0,0,w,h);
 const dados=linhas.slice().reverse();
 if(!dados.length){
  ctx.fillText("Aguardando sinais...",12,30);
  return;
 }
 const valores=dados.map(v=>Number(v.score)||0);
 const max=Math.max(3,...valores.map(Math.abs));
 const meio=h/2;
 ctx.beginPath();
 ctx.moveTo(0,meio);
 ctx.lineTo(w,meio);
 ctx.stroke();
 ctx.beginPath();
 valores.forEach((v,i)=>{
  const x=valores.length===1?w/2:i*w/(valores.length-1);
  const y=meio-(v/max)*(h/2-15);
  if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
 });
 ctx.lineWidth=2;
 ctx.stroke();
}
async function atualizar(){
 try{
  const r=await fetch('/api/v1/monitor/public-state');
  if(!r.ok) throw new Error('HTTP '+r.status);
  const d=await r.json();
  const x=d.latest||{},s=d.stats||{};
  const ultima=d.updated_at?new Date(d.updated_at).getTime():0;
  const online=ultima>0&&(Date.now()-ultima)<1200000;
  document.getElementById("conexao").textContent=online?"ONLINE":"SEM COMUNICAÇÃO";
  document.getElementById('sinal').textContent=x.sinal||'AGUARDAR';
  document.getElementById('taxa').textContent=(s.taxa_acerto??0)+'%';
  document.getElementById('avaliadas').textContent=s.avaliadas??0;
  document.getElementById('resultado').textContent=(s.acertos??0)+' / '+(s.erros??0);
  document.getElementById('detalhe').textContent=x.data_hora
   ? 'Horário de Brasília: '+horarioBR(x.data_hora)+' | Preço: '+(x.preco??'--')+' | Score: '+(x.score??'--')
   : 'Aguardando sincronização do monitor.';
  const linhas=(d.history||[]).slice().reverse().slice(0,30);
  desenharGrafico(linhas);
  document.getElementById('historico').innerHTML=linhas.length?linhas.map(v=>
   '<tr><td>'+horarioBR(v.data_hora)+'</td><td>'+(v.sinal||'--')+'</td><td>'+(v.score??'--')+'</td><td>'+(v.status||'--')+'</td><td>'+(v.resultado||'--')+'</td></tr>'
  ).join(''):'<tr><td colspan="5">Sem dados.</td></tr>';
  document.getElementById('atualizacao').textContent='Última sincronização: '+horarioBR(d.updated_at);
 }catch(e){
  document.getElementById('atualizacao').textContent='Erro ao atualizar: '+e.message;
 }
}
atualizar();
setInterval(atualizar,15000);
</script>
</body>
</html>
"""



if __name__== "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=80, reload=False)
