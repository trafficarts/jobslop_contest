# jobslop_contest

[English version](./README.en.md) | Детальнее на [канале Traffic Arts](https://t.me/trafficarts)

Кто пишет больше всего ИИ-слопа в вакансиях? Сравниваем работодателей и
рекрутеров с помощью трёх ИИ-детекторов: AI or Not, Pangram и GPTZero.

Датасет содержит 1 025 текстов вакансий, включая 30 синтетических текстов,
созданных ИИ для калибровки. Эксперимент отбирает 194 текста; 180 соответствуют
требованиям к длине и имеют сохранённые результаты всех трёх детекторов.

## Результаты

| Рекрутинговые агентства | Компании | Калибровка на ИИ и сравнение с рынком |
| :---: | :---: | :---: |
| <a href="images/agencies_chart.png"><img src="images/agencies_chart.png" alt="Сравнение рекрутинговых агентств по трём ИИ-детекторам" width="260"></a> | <a href="images/companies_chart.png"><img src="images/companies_chart.png" alt="Сравнение компаний по трём ИИ-детекторам" width="260"></a> | <a href="images/ai_and_aggregates_chart.png"><img src="images/ai_and_aggregates_chart.png" alt="Сравнение синтетических ИИ-текстов с рынком и вакансиями без установленного автора" width="260"></a> |

## Запуск

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python experiment.py
```

Исходные данные находятся в `data/dataset.json`. Результаты и заново построенные
графики сохраняются в `artifacts/`; в `images/` лежат графики для этого README.

```sh
.venv/bin/python experiment.py --no-charts  # Только JSON
.venv/bin/python -m unittest discover -s tests -v
```

## Методика и ограничения

Для каждого работодателя или агентства с минимум 8 исходными текстами берём
до 10 самых новых вакансий, подходящих для проверки. Для группы без установленного
автора — до 50. Текст подходит, если после удаления HTML и ссылок в нём остаётся
не менее 250 символов и 50 слов, содержащих буквы. Из общей выборки рынка исключены
LENKEP и синтетические ИИ-тексты.
