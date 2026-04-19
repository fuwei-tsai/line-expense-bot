# 🤖 LINE Expense Bot Backend (AI-Powered)

![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)
![Flask](https://img.shields.io/badge/Flask-Web%20Framework-lightgrey.svg)
![Gemini](https://img.shields.io/badge/Google%20Gemini-LLM-orange.svg)
![TiDB](https://img.shields.io/badge/TiDB-Cloud%20Database-blueviolet.svg)
![Vercel](https://img.shields.io/badge/Vercel-Deployed-black.svg)

This repository contains the backend webhook service for the **AI Expense Dashboard** project. It acts as the bridge between the LINE Messaging API and the TiDB cloud database, utilizing Google's Gemini LLM to parse unstructured natural language inputs into structured financial data.

👉 **[View the Frontend Dashboard Repository Here](https://github.com/Eve-tsai/ai-expense-dashboard)**

## ✨ Key Features

* 🧠 **Natural Language Processing (NLP):** Integrates `gemini-flash-lite` to interpret casual user messages (e.g., "Spent 15 CAD on lunch today") and automatically extract intent, amount, currency, and category.
* ⚡ **Serverless Deployment:** Deployed on **Vercel** for 24/7 high-availability and zero-maintenance serverless execution.
* 🚨 **Anomaly Detection:** Implements Z-score statistical modeling to automatically warn the user if a specific expense is significantly higher than their historical average for that category.
* 💱 **Multi-currency & Operations:** Supports inserting, updating, deleting, and complex currency exchange logging (e.g., TWD to CAD) purely through LINE chat.

## 🏗️ System Architecture

1.  **User Input:** User sends a natural language message via the LINE app.
2.  **Webhook Trigger:** LINE Platform sends an HTTP POST request to this Vercel-hosted Flask application.
3.  **AI Parsing:** The app forwards the text to the Google Gemini API with a strict prompting strategy to return a structured JSON object.
4.  **Data Processing:** The Flask app validates the JSON, calculates anomalies, and executes SQL commands (Insert/Update/Delete).
5.  **Cloud Storage:** Data is securely stored in a distributed **TiDB (MySQL)** cloud database.
6.  **Response:** The bot replies to the user with the recorded details or error alerts.

## 💻 Tech Stack

* **Backend Framework:** Python, Flask
* **Messaging API:** LINE Bot SDK
* **AI Model:** Google Generative AI (Gemini Flash-lite)
* **Database Connector:** PyMySQL
* **Hosting:** Vercel

## ⚙️ Local Development Setup

If you wish to run this backend locally:

1. Clone the repository:
```bash
git clone [https://github.com/Eve-tsai/line-expense-bot.git](https://github.com/Eve-tsai/line-expense-bot.git)
cd line-expense-bot
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```
3. Configure Environment Variables:
Create a .env file in the root directory and add your credentials (Ensure this file is in your .gitignore):
```bash
LINE_CHANNEL_ACCESS_TOKEN=your_line_token
LINE_CHANNEL_SECRET=your_line_secret
GEMINI_API_KEY=your_gemini_key
DB_HOST=your_tidb_host
DB_USER=your_db_user
DB_PASSWORD=your_db_password
DB_NAME=test
```

4. Run the Flask server:
```bash   
python line_bot_api.py
```









