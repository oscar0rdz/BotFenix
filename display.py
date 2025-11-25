import os
from core.portfolio import GlobalPortfolio
from strategy.scalper_logic import ScalperStrategy

def clear_console():
    """Clears the console."""
    os.system('cls' if os.name == 'nt' else 'clear')

def display_status(symbol: str, price: float, cvd: float, imbalance: float, vol: float, portfolio: GlobalPortfolio, strategy: ScalperStrategy, signal):
    """Displays a complete status panel in the console."""
    
    pos = portfolio.get_position(symbol)
    
    # --- Terminal Colors ---
    C_RED = '\033[91m'
    C_GREEN = '\033[92m'
    C_YELLOW = '\033[93m'
    C_BLUE = '\033[94m'
    C_CYAN = '\033[96m'
    C_END = '\033[0m'

    clear_console()
    
    print("--- SNIPER FLOW BOT ---")
    print(f"Symbol: {C_CYAN}{symbol.upper()}{C_END}")
    print("-" * 25)
    
    # --- Market Status ---
    print(f"{'Price:':<12} {C_YELLOW}{price:.2f}{C_END}")
    print(f"{'Imbalance:':<12} {C_BLUE}{imbalance:.3f}{C_END}")
    print(f"{'Current CVD:':<12} {C_BLUE}{cvd:.2f}{C_END}")
    if vol is not None:
        print(f"{'Volatility:':<12} {C_BLUE}{vol:.6f}{C_END}")
    
    print("-" * 25)
    
    # --- Strategy Status ---
    # NOTE: To show scores, we would need to modify the strategy to return them.
    # For now, we show the final signal, which is the most important thing.
    if signal:
        color = C_GREEN if signal.side == "LONG" else C_RED
        print(f"{'Active Signal:':<12} {color}{signal.side} @ {price:.2f} (Score: {signal.score:.2f}){C_END}")
    else:
        print(f"{'Active Signal:':<12} --")
        
    print("-" * 25)
    
    # --- Portfolio Status ---
    print(f"{'Global Equity:':<15} {C_GREEN}{portfolio.equity:.2f} USD{C_END}")
    if pos:
        if pos.side == "LONG":
            unrealized_pnl = (price - pos.entry_price) * pos.qty
        else:
            unrealized_pnl = (pos.entry_price - price) * pos.qty
            
        pnl_color = C_GREEN if unrealized_pnl >= 0 else C_RED
        print(f"{'Position:':<15} {pos.side} {pos.qty:.4f}")
        print(f"{'Entry:':<15} {pos.entry_price:.2f}")
        print(f"{'Unrealized PnL:':<15} {pnl_color}{unrealized_pnl:.4f} USD{C_END}")
        print(f"{'SL/TP:':<15} {pos.sl_price:.2f} / {pos.tp_price:.2f}")
    else:
        print("Position:         --")
        
    print("-" * 25)
    print("Press Ctrl+C to stop.")

