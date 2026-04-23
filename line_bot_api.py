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
        今天的日期是 {today_str}。請將記帳內容轉換為 JSON。Today's date is {today_str}. Please convert the accounting entries to JSON.
        
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
        print(f"Debug: analyzed text-> {clean_text}")
        return json.loads(clean_text)

    except Exception as e:
        print(f"Gemini analysis error: {e}")
        return None
    

# auto-generate display_id based on date and existing records
def generate_display_id(cursor, date_str):
    try:
        mmdd = date_str[5:7] + date_str[8:10] 
    except: 
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

                # 💡 Defense Mechanism: Large Amount Interception
                if amount > 9999999:
                    return {"status": "error", "message": "The amount entered is too large (exceeds system limits). Please verify if there are extra zeros."}

                anomaly_warning = ""
                # Exclude income and transfers from triggering expense warnings
                if category not in ['Income', 'Transfer', '收入', '轉帳']:
                    
                    #  Single Category Anomaly (Category Z-score)
                    cursor.execute("""
                        SELECT AVG(amount_original) as avg_amt, STDDEV(amount_original) as std_amt, COUNT(*) as cnt
                        FROM daily_expenses 
                        WHERE category = %s AND currency = %s AND amount_original > 0
                    """, (category, currency))
                    cat_stat = cursor.fetchone()
                    
                    category_alert = False
                    if cat_stat and cat_stat['cnt'] >= 5 and cat_stat['std_amt']:
                        cat_avg = float(cat_stat['avg_amt'])
                        cat_std = float(cat_stat['std_amt'])
                        
                        # Threshold: Greater than Average + 2 Standard Deviations
                        if amount > (cat_avg + 2 * cat_std):
                            category_alert = True
                            anomaly_warning += f"\n🚨 [Category Alert] This is significantly higher than your typical '{category}' spending ({cat_avg:.0f} {currency})."

                    # Monthly Burn Rate Anomaly (Monthly Z-score)
                    # Get the total expenses for the current month (including this new entry)
                    today = datetime.date.today()
                    cursor.execute("""
                        SELECT SUM(amount_original) as current_month_total
                        FROM daily_expenses 
                        WHERE YEAR(transaction_date) = %s AND MONTH(transaction_date) = %s 
                        AND currency = %s AND category NOT IN ('Income', 'Transfer', '收入', '轉帳')
                    """, (today.year, today.month, currency))
                    current_month_data = cursor.fetchone()
                    current_month_total = float(current_month_data['current_month_total'] or 0) + amount

                    # Get the total expenses for past months to calculate monthly avg and std
                    cursor.execute("""
                        SELECT SUM(amount_original) as monthly_total
                        FROM daily_expenses 
                        WHERE currency = %s AND category NOT IN ('Income', 'Transfer', '收入', '轉帳')
                        GROUP BY YEAR(transaction_date), MONTH(transaction_date)
                    """, (currency,))
                    historical_months = cursor.fetchall()
                    
                    if historical_months and len(historical_months) >= 2: # Requires at least 2 months of historical data
                        monthly_totals = [float(row['monthly_total']) for row in historical_months]
                        # Calculate mean and variance in Python memory
                        n = len(monthly_totals)
                        month_avg = sum(monthly_totals) / n
                        variance = sum([((x - month_avg) ** 2) for x in monthly_totals]) / n
                        month_std = variance ** 0.5
                        
                        # Threshold: Current month total exceeds (Monthly Average + 1.5 Standard Deviations)
                        if current_month_total > (month_avg + 1.5 * month_std):
                            if category_alert:
                                anomaly_warning += f"\n⚠️ [Monthly Alert] Furthermore, your total spending this month ({current_month_total:.0f} {currency}) has severely deviated from your norm!"
                            else:
                                anomaly_warning += f"\n⚠️ [Monthly Alert] While this expense is normal, your total spending this month ({current_month_total:.0f} {currency}) is approaching historic highs (Avg: {month_avg:.0f}). Watch your budget!"
               
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
    
    
    cat_mapping = {
        "飲食": "飲食 Food", "Food": "飲食 Food",
        "購物": "購物 Shopping", "Shopping": "購物 Shopping",
        "交通": "交通 Transport", "Transport": "交通 Transport",
        "生活": "生活 Living", "Living": "生活 Living",
        "娛樂": "娛樂 Entertainment", "Entertainment": "娛樂 Entertainment",
        "投資": "投資 Investment", "Investment": "投資 Investment",
        "收入": "收入 Income", "Income": "收入 Income",
        "轉帳": "轉帳 Transfer", "Transfer": "轉帳 Transfer"
    }
    
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
                    raw_cat = parsed_data.get('category')
                    display_cat = cat_mapping.get(raw_cat, raw_cat) 
                    is_income = raw_cat in ["收入", "Income"]      
                    sign = "+" if is_income else "-"
                    
                    reply_text = (
                        f"✅ 記帳成功 Success！\n"
                        f"編號 ID：{db_result['id']}\n"
                        f"日期 Date：{parsed_data.get('transaction_date')}\n"
                        f"品項 Item：{parsed_data.get('item_description')}\n"
                        f"分類 Category：{display_cat}\n"         
                        f"金額 Amount：{sign}{parsed_data.get('amount_original')} {parsed_data.get('currency')}"
                        f"{db_result.get('warning', '')}" 
                    )

                
                elif db_result["action"] == "update":
                    rec = db_result["record"]
                    raw_cat = rec.get('category')
                    display_cat = cat_mapping.get(raw_cat, raw_cat) 
                    is_income = raw_cat in ["收入", "Income"]       
                    sign = "+" if is_income else "-"
                    
                    reply_text = (
                        f"✏️ 修改成功 Revise Successful！\n"
                        f"編號 ID：{rec.get('display_id')}\n"
                        f"日期 Date：{rec.get('transaction_date')}\n"
                        f"品項 Item：{rec.get('item_description')}\n"
                        f"分類 Category：{display_cat}\n"         
                        f"金額 Amount：{sign}{rec.get('amount_original')} {rec.get('currency')}"
                    )
                
               
                elif db_result["action"] == "exchange":
                    reply_text = f"💱 換匯成功 Exchange Successful！\n減少：{db_result['from_info']}\n新增：{db_result['to_info']}\n已同步更新兩端看板。"
            else:
                reply_text = f"⚠️ 處理失敗 Failed：{db_result.get('message')}"

       
        elif intent == 'delete':
            raw_id = parsed_data.get('transaction_id')
            
            if raw_id:
                trans_id = str(raw_id).strip()
                
                if len(trans_id) == 5:
                    trans_id = "0" + trans_id
                    
                if delete_mysql_record_by_id(trans_id):
                    reply_text = f"🗑️ 刪除成功 Delete Successful！已移除編號 {trans_id} 紀錄。"
                else: 
                    reply_text = f"⚠️ 刪除失敗，找不到編號 {trans_id} 的紀錄 Delete Failed。"
            else:
                reply_text = "⚠️ 刪除失敗，AI 無法辨識要刪除的編號。 Failed to identify transaction ID."
                
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

if __name__ == "__main__":
    app.run()
