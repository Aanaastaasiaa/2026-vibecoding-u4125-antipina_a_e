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
[Промт для Cursor](files/promt2.md)
