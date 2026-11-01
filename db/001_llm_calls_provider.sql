-- Шаг 10: фактический провайдер и стоимость вызова по цене модели.
alter table llm_calls add column if not exists provider text;
alter table llm_calls add column if not exists cost_rub double precision;
