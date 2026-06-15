from datetime import timezone, datetime
from typing import Optional


class CexBotUtils:

    @staticmethod
    def _calculate_trade_age(entry_timestamp: Optional[str]) -> float:
        """Calcula a idade do trade em minutos de forma segura."""
        if not entry_timestamp:
            return 0.0
        try:
            entry_time = datetime.fromisoformat(entry_timestamp).replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - entry_time).total_seconds() / 60.0
        except Exception:
            return 0.0

    @staticmethod
    def check_viability_dynamic(pair: str, net_profit: float, amount_usdc: float, is_exit: bool,
                                spread_percent: float, entry_timestamp: Optional[str]) -> bool:
        """
        Validação de viabilidade com metas de 0.8% para entrada e decay linear para saída.
        """
        if not is_exit:
            return CexBotUtils._check_entry_viability(pair, net_profit, amount_usdc, spread_percent)
        return CexBotUtils._check_exit_viability(pair, net_profit, amount_usdc, spread_percent, entry_timestamp)

    @staticmethod
    def _check_entry_viability(pair: str, net_profit: float, amount_usdc: float, spread_percent: float) -> bool:
        """Gere exclusivamente a validação e o log da perna de Entrada."""
        min_profit = amount_usdc * 0.008
        success = net_profit >= min_profit and spread_percent >= 1.0

        if spread_percent > 0:
            gap_str = f" | Falta: ${min_profit - net_profit:.4f}" if net_profit < min_profit else " | ✅ PRONTO"
            print(
                f"🔍 [SCANNER] {pair} | Spread: {spread_percent:.2f}% | "
                f"Lucro Est: ${net_profit:.4f} | Alvo Min: ${min_profit:.4f}{gap_str}"
            )
        return success

    @staticmethod
    def _check_exit_viability(pair: str, net_profit: float, amount_usdc: float,
                              spread_percent: float, entry_timestamp: Optional[str]) -> bool:
        """Gere exclusivamente a validação, time-decay e o log da perna de Saída."""
        # 1. Time Decay em Linha Pura
        age_min = CexBotUtils._calculate_trade_age(entry_timestamp)
        factor = max(0.0, (60.0 - age_min) / 60.0)
        roi_target = 0.012 * factor

        min_profit_out = max(0.0, amount_usdc * roi_target) if roi_target > 0 else 0.0

        # 2. String de Progresso Simplificada
        if min_profit_out == 0:
            progress_str = "✅ PRONTO" if net_profit >= 0 else f"Faltam ${abs(net_profit):.4f}"
        else:
            progress_str = f"✅ PRONTO" if net_profit >= min_profit_out else f"{(net_profit / min_profit_out) * 100:.1f}%"

        # 3. Formatação do Log
        icon = "💰" if net_profit > 0 else "⏳"
        status_msg = (
            f"{icon} [MONITOR] {pair} | Idade: {age_min:.1f}m | Alvo ROI: {roi_target * 100:.2f}% | "
            f"Lucro: ${net_profit:.4f}/${min_profit_out:.2f} | Progresso: {progress_str} | Spread: {spread_percent:.2f}%"
        )

        if net_profit >= min_profit_out:
            print(f"\n✅ META DINÂMICA ALCANÇADA! {status_msg}")
            return True

        print(status_msg, end="\r")
        return False
