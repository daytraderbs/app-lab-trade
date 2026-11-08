def calculate_position_size(balance: float, risk_percentage: float, stop_loss_pips: float, pip_value_standard_lot: float = 10.0) -> dict:
    if balance <= 0 or risk_percentage <= 0 or stop_loss_pips <= 0:
        return {"error": "Valores de entrada devem ser maiores que zero."}
        
    money_at_risk = balance * (risk_percentage / 100)
    position_size_lots = money_at_risk / (stop_loss_pips * pip_value_standard_lot)
    
    return {
        "balance": float(balance),
        "risk_percentage": float(risk_percentage),
        "money_at_risk": round(float(money_at_risk), 2),
        "suggested_lot_size": round(float(position_size_lots), 2),
        "stop_loss_pips": float(stop_loss_pips)
    }
    
