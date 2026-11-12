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
