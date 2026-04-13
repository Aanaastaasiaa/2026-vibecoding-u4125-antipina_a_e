University: [ITMO University](https://itmo.ru/ru/)  
Faculty: [FICT](https://fict.itmo.ru)  
Course: [Vibe Coding: AI-боты для бизнеса](https://github.com/itmo-ict-faculty/vibe-coding-for-business)  
Year: 2025/2026  
Group: U4125  
Author: Antipina Anastasia Evgenievna  
Lab: Lab2
Date of create: 10.04.2025  
Date of finished: 

# Отчёт по лабораторной работе **«Подключение бота к данным»**
**«Подключение бота к данным»**  
*(Интеграция с The Cat API: факты и изображения)*  

## Шаг 1. Описание интеграции  

**Выбранный источник данных:** The Cat API (`https://thecatapi.com`)  
**Почему выбран именно этот вариант:**  
* API бесплатное с возможностью получения ключа за 1 минуту;  
* предоставляет структурированные данные в формате JSON (факты и изображения);  
* имеет чёткую документацию и стабильное время ответа;  
* кошки успокаивают людей, оставляющих обратную связь;  
* позволяет продемонстрировать работу с разными типами данных (текст, изображения) и обработку ошибок.
   
**Структура данных для изображний кошек:**  

{  
"id": "Hylo4Snaf",  
"url": "https://cdn.thedogapi.com/images/Hylo4Snaf.jpeg",  
"width": 1200,  
"height": 922,  
"mime_type": "image/jpeg"}  
  
**Структура данных для пород:**  
  
{"name": "Abyssinian",  
"cfa_url": "http://cfa.org/Breeds/BreedsAB/Abyssinian.aspx",  
"vetstreet_url": "http://www.vetstreet.com/cats/abyssinian",  
"vcahospitals_url": "https://vcahospitals.com/know-your-pet/cat-breeds/abyssinian",  
"temperament": "Active, Energetic, Independent, Intelligent, Gentle",  
"origin": "Egypt",  
"country_codes": "EG",  
"country_code": "EG",  
"life_span": "14 - 15",  
"intelligence": 5,  
"energy_level": 5,  
"child_friendly": 3,  
"dog_friendly": 4,  
"description": "The Abyssinian is easy to care for, and a joy to have in your home. They’re affectionate cats and love both people and other animals."}  

## Шаг 2. Промпт для LLM  
**Был создан промт для Cursor:**
  
[Промт для Cursor](files/promt2.md)  
  
**Выполненные итерации:**  
  
Итерация 1. Ошибка аутентификации — API требует API‑ключ.  
Решение: добавлена передача ключа в заголовке x-api-key, проверка наличия ключа перед запросом.  
  
Итерация 2. Проблемы с отправкой фото — URL может быть некорректным или недоступным.  
Решение: добавлена обработка ошибок отправки фото через try‑except, сообщение об ошибке пользователю.  
  
Итерация 3. Информация о породах написана на английском.  
Решение: добавлен автоперевод с использованием открытых api переводчиков.  

## Шаг 3. Реализация  
**Как работает интеграция:**  
  
* Пользователь отправляет /catfact.  
* Бот делает запрос к The Cat API с API‑ключом в заголовке.  
* Извлекает поле text из JSON‑ответа.  
* Если ошибка (сеть, пустой ответ, таймаут) — берёт случайный факт из BACKUP_FACTS.
* Отправляет текст пользователю.  
  
Аналогично для /catimage: получает url, отправляет фото с подписью или резерв.  
  
**Ключевые фрагменты кода**  
  
Получение факта о породе:  
* запрос с заголовком x-api-key;  
* проверка статуса ответа (200);  
* парсинг JSON, извлечение text;  
* fallback на random.choice(BACKUP_FACTS) при ошибке.  
  
Получение изображения:  

* запрос к /images/search;  
* извлечение url из массива;  
* отправка через reply_photo() с подписью;  
* обработка ошибок отправки (сообщение пользователю).  
  
Используемые библиотеки:  
  
* python-telegram-bot — взаимодействие с Telegram;  
* requests — HTTP‑запросы к The Cat API;  
* python-dotenv — загрузка CAT_API_KEY из .env;  
* logging — логирование запросов и ошибок;  
* random — выбор резервного факта. 
   
## Шаг 4. Реализация  
Видео‑демо:
<a href="[Видео с работой бота](https://disk.yandex.ru/d/UpqRUK7X4n-4Jg)">  
</a>  
Видео демонстрирует:  
* работу /catfact в штатном режиме и при ошибке API;  
* работу /catimage с отправкой фото и обработкой ошибки;  
* проверку сохранения функционала /feedback и /report;  
* настройку ежедневных напоминаний (/dailycat).

## Шаг 5. Выводы
Что получилось хорошо:  
* Бот успешно интегрирован с The Cat API для фактов и изображений.  
* Реализована отказоустойчивость: резервные данные при ошибках API.
* Логирование позволяет диагностировать проблемы.
* Весь существующий функционал сохранён без изменений.
* Код хорошо структурирован и прокомментирован.

Что можно улучшить:
* Добавить кэширование фактов и изображений на 1 час.  
* Реализовать команду /catgif для анимированных изображений (если API поддерживает).  

