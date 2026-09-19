"""Интеграция с Polygon API (https://polygon.codeforces.com/api).

Всё, что специфично для Polygon (подпись запросов, HTTP-клиент, чтение
`POLYGON_API_KEY`/`POLYGON_API_SECRET`, состояние привязки `problem_id` ->
Polygon problemId), живёт только здесь — не в общей логике оркестратора.

Ничего отсюда не импортируется на верхнем уровне пакета: модуль, который не
работает с Polygon, не тянет зависимость `requests`.
"""
