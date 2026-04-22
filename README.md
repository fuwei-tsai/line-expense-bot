<h1 align="center">🤖 Conversational AI Expense Tracking Agent </h1>

<p align="center">
  <a href="https://github.com/fuwei-tsai/ai-expense-dashboard"><img src="https://img.shields.io/badge/Frontend-Streamlit_Dashboard-FF4B4B?logo=streamlit" alt="Frontend"/></a>
  <a href="https://github.com/fuwei-tsai/line-expense-bot"><img src="https://img.shields.io/badge/Backend-Flask_Webhook-000000?logo=flask" alt="Backend"/></a>
  <img src="https://img.shields.io/badge/Model-Google_Gemini-8E75B2?logo=google" alt="Gemini"/>
  <img src="https://img.shields.io/badge/Database-TiDB_Cloud-3B5998" alt="TiDB"/>
</p>

> **Project Overview:** An end-to-end automated financial analytics system that utilizes Generative AI to transform unstructured natural language (chat messages) into structured, visual business insights. 

---

## 📑 Step 1: Identify a Business Use Case

**The Problem: User Friction in Personal Finance Management (PFM)**
Traditional expense tracking applications suffer from high cognitive load and "UI friction." Users must navigate through 4 to 5 steps (open app ➡️ select date ➡️ find category ➡️ input amount ➡️ save) just to log a single transaction. This friction leads to high user churn rates and inconsistent data entry.

**The Generative AI Solution & Business Value**
By implementing a **Conversational User Interface (CUI)** powered by Generative AI, we reduce the data entry process from multiple clicks to a single natural language message (e.g., *"Spent 15 CAD on lunch today"*). 
* **For Users:** Zero-friction logging using their existing messaging habits (LINE app).
* **For Business:** Increased Daily Active Users (DAU), higher data retention, and the creation of a high-quality financial dataset that can be used to drive personalized financial products or budget alerts.

---

## 🧠 Step 2: Model Selection

To achieve accurate real-time parsing, **Google Gemini (Flash-lite)** was selected as the core Large Language Model (LLM) for this agentic workflow. 

**Justification:**
1. **Semantic Understanding & Entity Extraction:** Unlike rule-based bots, Gemini handles fuzzy logic and mixed contexts (e.g., handling mixed currencies like CAD and TWD, or implicit categories).
2. **Low Latency:** Crucial for messaging platforms where users expect sub-second responses.
3. **Native JSON Generation:** Gemini excels at strictly outputting machine-readable JSON formats, which is mandatory for safe database insertion.
4. **Multilingual Prowess:** Perfectly supports Traditional Chinese and English interchangeably.

---

## ⚙️ Step 3: Model Adaptation

To ensure the LLM acts strictly as a data-extraction agent rather than a conversational chatbot, advanced **Prompt Engineering** and **Statistical Modeling** were applied.

* **Strict Prompt Formulation:** The system prompt forces the LLM to map unstructured text into a predetermined JSON schema: `{"date": "YYYY-MM-DD", "item": "str", "category": "str", "amount": "float", "currency": "str"}`.
* **Few-Shot Learning:** Embedded examples within the prompt to handle edge cases (e.g., negative amounts for expenses, positive for income).
* **Statistical Anomaly Detection (Z-score):** Once the AI extracts the data, the backend executes a Z-score algorithm against the user's historical database. If the new expense deviates significantly from their average, the system automatically triggers a budget warning.

---

## 🚀 Step 4: Implementation & Prototype

The prototype is fully functional and deployed using a microservices architecture. It acts as an autonomous agent that receives text, parses data, executes SQL commands, and visualizes KPIs.

### System Architecture
1. **User Interface:** LINE Messaging App (Input) & Streamlit Dashboard (Visualization).
2. **Agentic Backend (Python/Flask):** Deployed on **Vercel** serverless functions. It acts as the orchestrator.
3. **LLM Processing:** Google Gemini API extracts and standardizes the entities.
4. **Cloud Storage:** **TiDB Cloud (MySQL)** securely stores the structured transactions.

graph TD
    %% 定義樣式，讓圖表更美觀
    classDef user fill:#e1f5fe,stroke:#01579b,stroke-width:2px;
    classDef line fill:#e8f5e9,stroke:#1b5e20,stroke-width:2px;
    classDef agent fill:#fff9c4,stroke:#fbc02d,stroke-width:2px,stroke-dasharray: 5 5;
    classDef ai fill:#f3e5f5,stroke:#4a148c,stroke-width:2px;
    classDef db fill:#ede7f6,stroke:#311b92,stroke-width:2px;
    classDef dashboard fill:#fff3e0,stroke:#e65100,stroke-width:2px;

    %% 定義節點 (Nodes)
    UserNode[("👤 使用者 User\n(LINE App)")]:::user
    LineApiNode[("💬 LINE Messaging API\n(網關 Gateway)")]:::line
    
    subgraph Agentic_Workflow [🧠 代理人核心邏輯 Agent Orchestrator (Vercel/Flask)]
        WebhookNode("接收請求 & 驗證\nExtract Raw Text"):::agent
        PromptEngNode("提示詞工程\nSystem Prompt / Few-shot"):::agent
        LogicNode{"數據驗證 &\n統計運算 (Z-score)"}:::agent
        SqlNode("執行 SQL 指令\nInsert/Select"):::agent
        ReplyNode("組合回覆訊息\nFormat Response"):::agent
    end

    GeminiNode[("🤖 Google Gemini AI\n(NLP 解析 / 實體擷取)")]:::ai
    TiDbNode[("🗄️ TiDB Cloud DB\n(MySQL 分散式儲存)")]:::db
    StreamlitNode[("📊 數據看板\n(Streamlit Cloud)")]:::dashboard

    %% 定義連線流程 (Edges)
    
    %% 輸入資料流
    UserNode -->|1. 發送自然語言訊息\n(e.g., 今天晚餐 20 CAD)| LineApiNode
    LineApiNode -->|2. Webhook 轉發 (POST Request)| WebhookNode
    
    %% AI 解析流
    WebhookNode -->|3. 傳送原始文本 + 提示詞| GeminiNode
    GeminiNode -->|4. 回傳結構化 JSON 資料| LogicNode
    
    %% 資料庫交互流
    LogicNode -->|5a. 資料庫寫入 (正常交易)| SqlNode
    LogicNode -.->|5b. 資料庫讀取 (歷史平均值)| SqlNode
    SqlNode ==>|6. SQL 交互| TiDbNode
    
    %% 回饋流
    SqlNode --> ReplyNode
    ReplyNode -->|7. 發送回覆 (Reply Token)| LineApiNode
    LineApiNode -->|8. 將記帳結果送回手機| UserNode

    %% 看板流 (獨立)
    UserNode -->|9. 查看財務分析| StreamlitNode
    StreamlitNode == >|10. 讀取即時數據| TiDbNode

    %% 標註關鍵技術
    linkStyle 2,3 stroke:#4a148c,stroke-width:2px;
    linkStyle 5,6 stroke:#311b92,stroke-width:2px;


### 🎥 Demonstration
*(💡 Note: Add your screenshots or a GIF here to prove it works!)*

| 1. Zero-Friction Input (LINE Bot) | 2. Real-Time Analytics (Streamlit) |
| :---: | :---: |
| <img src="https://via.placeholder.com/250x400.png?text=Add+LINE+Screenshot+Here" width="250"/> | <img src="https://via.placeholder.com/400x300.png?text=Add+Dashboard+Screenshot+Here" width="400"/> |

**How it solves the problem:** The user simply types naturally. The AI Agent intercepts the text, structures it, logs it into the cloud, and the Streamlit dashboard instantly updates the budget burn rate and expense charts—completely eliminating traditional UI friction.

---
*Developed by [Fuwei Tsai](https://github.com/fuwei-tsai)*
