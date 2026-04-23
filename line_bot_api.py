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

# 1. Set environment variables
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

DB_HOST = os.environ.get('DB_HOST')
DB_USER = os.environ.get('DB_USER')
DB_PASSWORD = os.environ.get('DB_PASSWORD')
DB_NAME = os.environ.get('DB_NAME')

# Initialize LINE Bot API and Gemini API
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 2. Gemini function to parse user input into structured data
def parse_expense_with_gemini(user_text):
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        target_model = "models/gemini-flash-lite-latest"
        print(f"Debug: target model -> {target_model}")

        model = genai.GenerativeModel(target_model)
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        
        prompt = f"""
        Today's date is {today_str}. Please convert the user's input into JSON format.
        
        [Rules]
        1. Determine the category:
           - If it involves earning money, salary, or investment profits, categorize as "Income".
           - If it detects "exchange" or "currency conversion", categorize as "Transfer".
           - 💡 [Crucial Addition] If it involves fixed living expenses like rent, water, electricity, gas, telecom, internet, or subscriptions (Netflix/Spotify), the category MUST be "Living".
           - Otherwise, accurately categorize as "Food", "Shopping", "Transport", "Investment", or "Entertainment".
        2. Determine the intent: new record = "record", modify = "update", delete = "delete", currency exchange = "exchange".
        3. If intent is "update", you must extract the "transaction_id" specified by the user, along with the values to be modified.
        4. If intent is "delete", you must extract the "transaction_id" specified by the user.
        5. Strictly prohibit using English column names as values.

        [Currency Exchange Rules]
        - If the user says "Convert Currency A to Currency B", set intent to "exchange".
        - The JSON must include:
            1. from_currency, from_amount (negative value)
            2. to_currency, to_amount (positive value)
            3. item_description fixed as "Currency Exchange"
            4. category for both must be "Transfer" (to avoid impacting pure expense statistics)

        [Example 1: Record]
        Input: "Dinner 25 CAD today"
        Output: {{
            "intent": "record",
            "transaction_date": "{today_str}",
            "item_description": "Dinner",
            "category": "Food",
            "amount_original": 25,
            "currency": "CAD"
        }}

        [Example 2: Update]
        Input: "Change the amount of ID 041801 to 20 CAD"
        Output: {{
            "intent": "update",
            "transaction_id": "041801",  
            "amount_original": 20,
            "currency": "CAD"
        }}

        [Example 3: Delete]
        Input: "Delete record number 5"
        Output: {{
            "intent": "delete",
            "transaction_id": "5"
        }}

        [Example 4: Income]
        Input: "Stock income 10000 TWD"
        Output: {{
            "intent": "record",
            "transaction_date": "{today_str}",
            "item_description": "Stock Income",
            "category": "Income",
            "amount_original": 10000,
            "currency": "TWD"
        }}

        [Example 5: Exchange]
        Input: "Convert 100 TWD to 4 CAD"
        Output: {{
            "intent": "exchange",
            "transaction_date": "{today_str}",
            "from_currency": "TWD",
            "from_amount": 100,
            "to_currency": "CAD",
            "to_amount": 4
        }}

        User Input: {user_text}
        """
        
        response = model.generate_content(prompt)
        clean_text = response.text.replace('```json', '').replace('```', '').strip()
        print(f"Debug: analyzed text -> {clean_text}")
        return json.loads(clean_text)

    except Exception as e:
        print(f"Gemini analysis error: {e}")
        return None
    

# Auto-generate display_id based on date and existing records
def generate_display_id(cursor, date_str):
    try:
        mmdd = date_str[5:7] + date_str[8:10] 
    except: 
        mmdd = "0000"
        
    # Search for existing display_id with the same date prefix to determine the next sequence number
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
    

# 3. MySQL writing logic for record, update, and exchange
def process_database(data):
    try:
        intent = data.get('intent', 'record')
        
        if str(data.get('transaction_date')) == 'transaction_date' or str(data.get('item_description')) == 'item_description':
            return {"status": "error", "message": "AI incorrectly formatted the date or item description. Please try again."}

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
                
                # Z-score outlier detection for anomaly warning (excludes Income and Transfer)
                anomaly_warning = ""
                if category not in ['Income', 'Transfer', '收入', '轉帳']:
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
                            anomaly_warning = f"\n🚨 [Anomaly Alert] This expense is significantly higher than your typical spending in '{category}' ({avg_amt:.0f} {currency}). Please be mindful of your budget!"

                # Insert new record into MySQL
                sql = "INSERT INTO daily_expenses (transaction_date, item_description, category, amount_original, currency, amount_base, display_id) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                val = (data.get('transaction_date'), data.get('item_description'), category, amount, currency, amount, new_disp_id)
                cursor.execute(sql, val)
                connection.commit()
                
                return {"status": "success", "action": "insert", "id": new_disp_id, "warning": anomaly_warning}
            
            elif intent == 'update':
                trans_id = str(data.get('transaction_id')) 
                if not trans_id: return {"status": "error", "message": "Cannot identify the transaction ID to update. Please provide a valid ID."}
                
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
                cursor.execute(sql, (date, "Exchange (Out)", "Transfer", -abs(from_amt), from_cur, -abs(from_amt), id_1))
                
                id_2 = generate_display_id(cursor, date)
                cursor.execute(sql, (date, "Exchange (In)", "Transfer", abs(to_amt), to_cur, abs(to_amt), id_2))
                
                connection.commit()
                return {"status": "success", "action": "exchange", "from_info": f"-{abs(from_amt)} {from_cur}", "to_info": f"+{abs(to_amt)} {to_cur}"}
            
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        if 'connection' in locals() and connection.open:
            connection.close()



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
                time_label = "Current Month"
            elif time_frame == 'this_week':
                sql = """
                    SELECT currency, SUM(amount_original) as total_amount 
                    FROM daily_expenses 
                    WHERE YEARWEEK(transaction_date, 1) = YEARWEEK(%s, 1)
                    GROUP BY currency
                """
                cursor.execute(sql, (today,))
                time_label = "Current Week"
            else: 
                sql = """
                    SELECT currency, SUM(amount_original) as total_amount 
                    FROM daily_expenses 
                    WHERE transaction_date = %s
                    GROUP BY currency
                """
                cursor.execute(sql, (today,))
                time_label = "Today"
                
            results = cursor.fetchall()
            
        connection.close()
        return time_label, results
    except Exception as e:
        print(f"Failed to query: {e}")
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
        print(f"Failed to delete: {e}")
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

# 7. LINE response logic
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = event.message.text
    parsed_data = parse_expense_with_gemini(user_text)
    reply_text = "❌ The system is currently busy or unable to parse the input. Please try again later."
    
    if parsed_data:
        intent = parsed_data.get('intent')
        
        if intent == 'query':
            time_label, results = query_expenses_from_mysql(parsed_data.get('time_frame', 'today'))
            if results:
                lines = [f"📊 [Total Expenses: {time_label}]"]
                for row in results:
                    if row.get('total_amount'):
                        lines.append(f"💰 {row['currency']} : {float(row['total_amount']):g}")
                reply_text = "\n".join(lines) if len(lines) > 1 else f"📊 No records found for {time_label}."
            else: 
                reply_text = f"📊 No records found for {time_label}."
                
        elif intent in ['record', 'update', 'exchange']: 
            db_result = process_database(parsed_data) 
            
            if db_result.get("status") == "success":
                if db_result["action"] == "insert":
                    is_income = parsed_data.get('category') in ["Income", "收入"]
                    sign = "+" if is_income else "-"
                    reply_text = (
                        f"✅ Entry Successful!\n"
                        f"ID: {db_result['id']}\
