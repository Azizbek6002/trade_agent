import logging
import sqlite3
import pandas as pd
from pathlib import Path
from config import config

logger = logging.getLogger(__name__)

class TradeJournalExporter:
    """
    Exports error database and trade journal statistics to xatolar.xlsx.
    """
    def __init__(self, db_path: str = config.DB_PATH, excel_path: str = config.EXCEL_EXPORT_PATH):
        self.db_path = db_path
        self.excel_path = excel_path

    def export_to_excel(self) -> str:
        """Reads trades and error_journal tables and exports to xatolar.xlsx."""
        Path(self.excel_path).parent.mkdir(parents=True, exist_ok=True)
        
        try:
            conn = sqlite3.connect(self.db_path)
            
            df_trades = pd.read_sql_query("SELECT * FROM trades", conn)
            df_losses = pd.read_sql_query("SELECT * FROM trades WHERE pnl_usd < 0", conn)
            
            with pd.ExcelWriter(self.excel_path, engine='openpyxl') as writer:
                df_trades.to_excel(writer, sheet_name="Barcha Savdolar", index=False)
                df_losses.to_excel(writer, sheet_name="Zararli Savdolar", index=False)
                
            conn.close()
            logger.info(f"Excel export successful: {self.excel_path}")
            return self.excel_path
        except Exception as e:
            logger.error(f"Failed to export to Excel: {e}")
            return ""

trade_journal_exporter = TradeJournalExporter()
