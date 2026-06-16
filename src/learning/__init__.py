"""Öğrenme / geri besleme katmanı.

Botun ürettiği sinyallerin sonuçlarını geçmiş veriyle değerlendirir (evaluator),
başarı istatistiği çıkarır (stats) ve güven skorunu geçmiş isabete göre kalibre
eder (calibrator). Böylece bot kendi geçmişinden "öğrenir".
"""
