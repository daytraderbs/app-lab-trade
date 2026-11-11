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
body{margin:0;background:#101827;color:#eef3ff;font-family:Arial,sans-serif;padding:20px}
.container{max-width:1000px;margin:auto}
h1{color:#50d6a0}
.card{background:#1b293c;padding:20px;border-radius:12px;margin:12px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.valor{font-size:25px;font-weight:bold;margin-top:10px}
table{width:100%;border-collapse:collapse}
td,th{padding:10px;border-bottom:1px solid #35445a;text-align:left}
.menor{color:#aebbd0}
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
</div>
<div class="card">
<h3>Último sinal</h3>
<div id="detalhe" class="menor">Aguardando sincronização do monitor.</div>
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
async function atualizar(){
 try{
  const r=await fetch('/api/v1/monitor/public-state');
  if(!r.ok) throw new Error('HTTP '+r.status);
  const d=await r.json();
  const x=d.latest||{},s=d.stats||{};
  document.getElementById('sinal').textContent=x.sinal||'AGUARDAR';
  document.getElementById('taxa').textContent=(s.taxa_acerto??0)+'%';
  document.getElementById('avaliadas').textContent=s.avaliadas??0;
  document.getElementById('resultado').textContent=(s.acertos??0)+' / '+(s.erros??0);
  document.getElementById('detalhe').textContent=x.data_hora
   ? 'Horário: '+x.data_hora+' | Preço: '+(x.preco??'--')+' | Score: '+(x.score??'--')
   : 'Aguardando sincronização do monitor.';
  const linhas=(d.history||[]).slice().reverse().slice(0,30);
  document.getElementById('historico').innerHTML=linhas.length?linhas.map(v=>
   '<tr><td>'+(v.data_hora||'--')+'</td><td>'+(v.sinal||'--')+'</td><td>'+(v.score??'--')+'</td><td>'+(v.status||'--')+'</td><td>'+(v.resultado||'--')+'</td></tr>'
  ).join(''):'<tr><td colspan="5">Sem dados.</td></tr>';
  document.getElementById('atualizacao').textContent='Última sincronização: '+(d.updated_at||'Ainda não recebida');
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
