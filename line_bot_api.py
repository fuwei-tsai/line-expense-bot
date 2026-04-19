from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import google.generativeai as genai   
import pymysql
import json
import os
import datetime

app = Flask(__name__)

# 1. setting environment variables
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

DB_HOST = os.environ.get('DB_HOST')
DB_USER = os.environ.get('DB_USER')
DB_PASSWORD = os.environ.get('DB_PASSWORD')
DB_NAME = os.environ.get('DB_NAME')

# init LINE Bot API and Gemini API
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 2. Gemini function to parse user input into structured data
def parse_expense_with_gemini(user_text):
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        target_model = "models/gemini-flash-lite-latest"
        print(f"Debug: decide model-> {target_model}")

        model = genai.GenerativeModel(target_model)
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        
        prompt = f"""
        今天的日期是 {today_str}。請將記帳內容轉換為 JSON。
        
        【規則】
        1. 判斷分類 (category)：
           - 若是賺錢、領薪水、投資獲利，分類為 "收入"。
           - 偵測到「換成」、「換匯」，分類為 "轉帳"。
           - 💡 【重點新增】若是房租、水電費、瓦斯費、電信費、網路費、訂閱費(Netflix/Spotify) 等日常固定開銷，分類必須標註為 "生活"。
           - 否則，請精準歸類為 "飲食"、"購物"、"交通"、"投資" 或 "娛樂"。
        2. 判斷意圖 (intent)：新增為 "record"、修改為 "update"、刪除為 "delete"、換匯為 "exchange"。
        3. 若 intent 為 "update"，必須提取使用者指定的「編號」(transaction_id)，以及想修改的值。
        4. 若 intent 為 "delete"，必須提取使用者指定的「編號」(transaction_id)。
        5. 嚴禁在值(Value)中填入英文欄位名稱。

        【換匯規則】
        - 若使用者說「A幣換成B幣」，intent 設為 "exchange"。
        - 必須包含：
            1. from_currency, from_amount (負數)
            2. to_currency, to_amount (正數)
            3. item_description 固定為 "換匯"
            4. category 兩筆皆為 "轉帳" (這樣不會影響你的純支出統計)

        【範例 1：新增】
        輸入：「今天晚餐 25 加幣」
        輸出：{{
            "intent": "record",
            "transaction_date": "{today_str}",
            "item_description": "晚餐",
            "category": "飲食",
            "amount_original": 25,
            "currency": "CAD"
        }}

        【範例 2：修改】
        輸入：「請將編號 041801 的金額改為 20 加幣」
        輸出：{{
            "intent": "update",
            "transaction_id": "041801",  # 💡 必須是雙引號包住的字串！
            "amount_original": 20,
            "currency": "CAD"
        }}

        【範例 3：刪除】
        輸入：「請刪除編號 5 的紀錄」
        輸出：{{
            "intent": "delete",
            "transaction_id": 5
        }}

        【正確輸出範例：收入】
        輸入：「股票收入 10000 台幣」
        輸出：{{
            "intent": "record",
            "transaction_date": "{today_str}",
            "item_description": "股票收入",
            "category": "收入",
            "amount_original": 10000,
            "currency": "TWD"
        }}

        【範例：換匯】
        輸入：「100 台幣換成 4 加幣」
        輸出：{{
            "intent": "exchange",
            "transaction_date": "{today_str}",
            "from_currency": "TWD",
            "from_amount": 100,
            "to_currency": "CAD",
            "to_amount": 4
        }}

        使用者輸入：{user_text}
        """
        
        response = model.generate_content(prompt)
        clean_text = response.text.replace('```json', '').replace('```', '').strip()
        print(f"Debug: analyzed text-> {clean_text}")
        return json.loads(clean_text)

    except Exception as e:
        print(f"Gemini analysis error: {e}")
        return None
    

# auto-generate display_id based on date and existing records
def generate_display_id(cursor, date_str):
    try:
        mmdd = date_str[5:7] + date_str[8:10] 
    except: # 💡 修正 1：補上遺失的 except 區塊
        mmdd = "0000"
        
    # search for existing display_id with same date prefix to determine next sequence number
    cursor.execute("SELECT display_id FROM daily_expenses WHERE display_id LIKE %s ORDER BY display_id DESC LIMIT 1", (f"{mmdd}%",))
    result = cursor.fetchone()
    
    if result and result['display_id'] and len(result['display_id']) >= 6:
        try:
            last_seq = int(result['display_id'][-2:])
            new_seq = last_seq + 1
        except:
            new_seq = 1
    else:
        new_seq = 1
        
    return f"{mmdd}{new_seq:02d}"
    

# 3. MySQL writing logic for both record and update (and exchange)
def process_database(data):
    try:
        intent = data.get('intent', 'record')
        
        if str(data.get('transaction_date')) == 'transaction_date' or str(data.get('item_description')) == 'item_description':
            return {"status": "error", "message": "AI wrong formatted the date or item description. Please try again."}

        connection = pymysql.connect(
            host=DB_HOST, port=4000, user=DB_USER, password=DB_PASSWORD, database=DB_NAME,
            ssl_verify_cert=True, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor
        )
        
        with connection.cursor() as cursor:
            if intent == 'record':
                new_disp_id = generate_display_id(cursor, data.get('transaction_date'))
                category = data.get('category')
                currency = data.get('currency', 'CAD')
                amount = float(data.get('amount_original', 0))
                
                # Z-score outlier detection for anomaly warning (only for non-income and non-transfer categories)
                anomaly_warning = ""
                if category not in ['收入', '轉帳']:
                    cursor.execute("""
                        SELECT AVG(amount_original) as avg_amt, STDDEV(amount_original) as std_amt, COUNT(*) as cnt
                        FROM daily_expenses 
                        WHERE category = %s AND currency = %s AND amount_original > 0
                    """, (category, currency))
                    stat = cursor.fetchone()
                    
                    if stat and stat['cnt'] >= 5 and stat['std_amt']:
                        avg_amt = float(stat['avg_amt'])
                        std_amt = float(stat['std_amt'])
                        
                        if amount > (avg_amt + 2 * std_amt):
                            anomaly_warning = f"\n🚨 【Anomaly Alert】this expense is significantly higher than your typical spending in 「{category}」 ({avg_amt:.0f} {currency}), please be mindful of your budget!"

                # insert new record into MySQL
                sql = "INSERT INTO daily_expenses (transaction_date, item_description, category, amount_original, currency, amount_base, display_id) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                val = (data.get('transaction_date'), data.get('item_description'), category, amount, currency, amount, new_disp_id)
                cursor.execute(sql, val)
                connection.commit()
                
                return {"status": "success", "action": "insert", "id": new_disp_id, "warning": anomaly_warning}
            
            elif intent == 'update':
                trans_id = str(data.get('transaction_id')) 
                if not trans_id: return {"status": "error", "message": "cannot identify the transaction ID to update. Please provide a valid ID."}
                
                updates = []
                vals = []
                if 'amount_original' in data:
                    updates.append("amount_original = %s, amount_base = %s")
                    vals.extend([data['amount_original'], data['amount_original']])
                if 'currency' in data:
                    updates.append("currency = %s")
                    vals.append(data['currency'])
                if 'item_description' in data:
                    updates.append("item_description = %s")
                    vals.append(data['item_description'])
                if 'category' in data:
                    updates.append("category = %s")
                    vals.append(data['category'])
                if 'transaction_date' in data:
                    updates.append("transaction_date = %s")
                    vals.append(data['transaction_date'])
                    
                if not updates: return {"status": "error", "message": "No update content provided."}
                
                sql = f"UPDATE daily_expenses SET {', '.join(updates)} WHERE display_id = %s OR id = %s"
                vals.extend([trans_id, trans_id])
                
                cursor.execute(sql, tuple(vals))
                connection.commit()
                
                cursor.execute("SELECT * FROM daily_expenses WHERE display_id = %s OR id = %s", (trans_id, trans_id))
                updated_record = cursor.fetchone()
                return {"status": "success", "action": "update", "record": updated_record}

            elif intent == 'exchange':
                from_cur = data.get('from_currency', 'TWD')
                from_amt = float(data.get('from_amount', 0))
                to_cur = data.get('to_currency', 'CAD')
                to_amt = float(data.get('to_amount', 0))
                date = data.get('transaction_date')

                sql = "INSERT INTO daily_expenses (transaction_date, item_description, category, amount_original, currency, amount_base, display_id) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                
                id_1 = generate_display_id(cursor, date)
                cursor.execute(sql, (date, "exchange (out)", "transfer", -abs(from_amt), from_cur, -abs(from_amt), id_1))
                
                id_2 = generate_display_id(cursor, date)
                cursor.execute(sql, (date, "exchange (in)", "transfer", abs(to_amt), to_cur, abs(to_amt), id_2))
                
                connection.commit()
                return {"status": "success", "action": "exchange", "from_info": f"-{abs(from_amt)} {from_cur}", "to_info": f"+{abs(to_amt)} {to_cur}"}
            
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        if 'connection' in locals() and connection.open:
            connection.close()

# 💡 修正 2：已刪除原本流浪在外的幽靈更新函數 (因其功能已整合進上方 process_database 內)

# 4. MySQL query logic for the "query" intent
def query_expenses_from_mysql(time_frame):
    try:
        connection = pymysql.connect(
            host=DB_HOST, port=4000, user=DB_USER, password=DB_PASSWORD,
            database=DB_NAME, ssl_verify_cert=True, ssl_verify_identity=True,
            cursorclass=pymysql.cursors.DictCursor
        )
        
        with connection.cursor() as cursor:
            today = datetime.date.today()
            
            if time_frame == 'this_month':
                sql = """
                    SELECT currency, SUM(amount_original) as total_amount 
                    FROM daily_expenses 
                    WHERE YEAR(transaction_date) = %s AND MONTH(transaction_date) = %s
                    GROUP BY currency
                """
                cursor.execute(sql, (today.year, today.month))
                time_label = "Current Month 本月"
            elif time_frame == 'this_week':
                sql = """
                    SELECT currency, SUM(amount_original) as total_amount 
                    FROM daily_expenses 
                    WHERE YEARWEEK(transaction_date, 1) = YEARWEEK(%s, 1)
                    GROUP BY currency
                """
                cursor.execute(sql, (today,))
                time_label = "Current Week 本週"
            else: 
                sql = """
                    SELECT currency, SUM(amount_original) as total_amount 
                    FROM daily_expenses 
                    WHERE transaction_date = %s
                    GROUP BY currency
                """
                cursor.execute(sql, (today,))
                time_label = "Today 今天"
                
            results = cursor.fetchall()
            
        connection.close()
        return time_label, results
    except Exception as e:
        print(f"查詢失敗 Failed to query: {e}")
        return None, None


# 5. MySQL delete logic
def delete_mysql_record_by_id(record_id):
    try:
        connection = pymysql.connect(
            host=DB_HOST, port=4000, user=DB_USER, password=DB_PASSWORD, database=DB_NAME,
            ssl_verify_cert=True, charset='utf8mb4'
        )
        with connection.cursor() as cursor:
            sql = "DELETE FROM daily_expenses WHERE display_id = %s OR id = %s"
            cursor.execute(sql, (record_id,))
            connection.commit()
            return cursor.rowcount > 0 
    except Exception as e:
        print(f"刪除失敗 Failed to delete: {e}")
        return False
    finally:
        if 'connection' in locals() and connection.open:
            connection.close()

# 6. LINE Webhook receive and signature verification
@app.route("/webhook", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 7. Line response logic
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = event.message.text
    parsed_data = parse_expense_with_gemini(user_text)
    reply_text = "❌ System is currently busy or unable to parse the input. Please try again later. 請稍後再試"
    
    if parsed_data:
        intent = parsed_data.get('intent')
        
        if intent == 'query':
            time_label, results = query_expenses_from_mysql(parsed_data.get('time_frame', 'today'))
            if results:
                lines = [f"📊 【{time_label}花費總計 Total Expenses】"]
                for row in results:
                    if row.get('total_amount'):
                        lines.append(f"💰 {row['currency']} : {float(row['total_amount']):g}")
                reply_text = "\n".join(lines) if len(lines) > 1 else f"📊 【{time_label}】目前無紀錄 No record。"
            else: 
                reply_text = f"📊 【{time_label}】目前無紀錄 No record。"
                
        elif intent in ['record', 'update', 'exchange']: 
            db_result = process_database(parsed_data) 
            
            if db_result.get("status") == "success":
                if db_result["action"] == "insert":
                    is_income = parsed_data.get('category') == "收入"
                    sign = "+" if is_income else "-"
                    reply_text = (
                        f"✅ 記帳成功 Success！\n"
                        f"編號 ID：{db_result['id']}\n"
                        f"日期 Date：{parsed_data.get('transaction_date')}\n"
                        f"品項 Item：{parsed_data.get('item_description')}\n"
                        f"分類 Category：{parsed_data.get('category')}\n"
                        f"金額 Amount：{sign}{parsed_data.get('amount_original')} {parsed_data.get('currency')}"
                        f"{db_result.get('warning', '')}" 
                    )

                elif db_result["action"] == "update":
                    rec = db_result["record"]
                    is_income = rec.get('category') == "收入"
                    sign = "+" if is_income else "-"
                    reply_text = (
                        f"✏️ 修改成功 Revise Successful！\n"
                        f"編號 ID：{rec.get('id')}\n"
                        f"日期 Date：{rec.get('transaction_date')}\n"
                        f"品項 Item：{rec.get('item_description')}\n"
                        f"分類 Category：{rec.get('category')}\n"
                        f"金額 Amount：{sign}{rec.get('amount_original')} {rec.get('currency')}"
                    )
                
                elif db_result["action"] == "exchange":
                    # 💡 修正 3：對應 process_database 回傳正確的變數名稱
                    reply_text = f"💱 換匯成功 Exchange Successful！\n減少：{db_result['from_info']}\n新增：{db_result['to_info']}\n已同步更新兩端看板。"
            else:
                reply_text = f"⚠️ 處理失敗 Failed：{db_result.get('message')}"

        elif intent == 'delete':
            trans_id = parsed_data.get('transaction_id')
            if trans_id:
                if delete_mysql_record_by_id(trans_id):
                    reply_text = f"🗑️ 刪除成功 Delete Successful！已移除編號 {trans_id} 紀錄。"
                else: 
                    reply_text = f"⚠️ 刪除失敗，找不到編號 {trans_id} 的紀錄 Delete Failed。"
            else:
                reply_text = "⚠️ 刪除失敗，AI 無法辨識要刪除的編號。 failed to identify transaction ID to delete. Please provide a valid ID."
                
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

if __name__ == "__main__":
    app.run()