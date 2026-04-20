import requests
import logging
import os
from datetime import datetime

class TelegramNotifier:
    def __init__(self, bot_token, chat_id):
        self.bot_token = bot_token
        # Support multiple chat IDs
        if isinstance(chat_id, str) and ',' in chat_id:
            self.chat_ids = [id.strip() for id in chat_id.split(',') if id.strip()]
        elif chat_id:
            self.chat_ids = [chat_id]
        else:
            self.chat_ids = []
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        
        # Owner ID for access control
        self.owner_id = os.environ.get("TELEGRAM_OWNER_ID", "").strip()
        if self.owner_id:
            self.owner_id = str(self.owner_id)  # Ensure it's a string
            
        # Remove webhook setup for now - we'll handle commands differently
        
    def send_message(self, message):
        """Send a message to Telegram"""
        if not self.bot_token or not self.chat_ids:
            logging.warning("Telegram credentials not configured")
            return False
            
        success_count = 0
        for chat_id in self.chat_ids:
            try:
                url = f"{self.base_url}/sendMessage"
                data = {
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "HTML"
                }
                
                response = requests.post(url, data=data, timeout=10)
                response.raise_for_status()
                success_count += 1
            except Exception as e:
                logging.error(f"Failed to send Telegram message to {chat_id}: {e}")
                
        if success_count > 0:
            logging.info(f"Telegram message sent to {success_count} chats")
            return True
        return False
    
    def send_current_position(self, position, current_price, balance=0):
        """Send notification about current open position"""
        if not position:
            message = """
📊 <b>CURRENT POSITION</b>

💤 No open position at the moment.
💳 <b>Balance:</b> ${:.2f}
            """.format(balance).strip()
        else:
            side_emoji = "📈" if position["side"] == "long" else "📉"
            side_text = "LONG" if position["side"] == "long" else "SHORT"
            trade_number = position.get("trade_number", 1)
            
            # Calculate current P&L
            entry_price = position["entry_price"]
            size = position["size_base"]
            if position["side"] == "long":
                pnl = (current_price - entry_price) * size
            else:
                pnl = (entry_price - current_price) * size
            
            pnl_emoji = "💚" if pnl > 0 else "❤️"
            pnl_sign = "+" if pnl > 0 else ""
            
            # Calculate ROI
            margin = position["notional"] / 200
            roi = (pnl / margin) * 100 if margin > 0 else 0
            
            message = f"""
📊 <b>CURRENT POSITION #{trade_number}</b>
{side_emoji} <b>{side_text} SOL/USDT</b> (10.0% of bank)

💰 <b>Entry Price:</b> ${entry_price:.2f}
💰 <b>Current Price:</b> ${current_price:.2f}
🎯 <b>Size:</b> {size:.4f} SOL
💼 <b>Notional:</b> ${position["notional"]:.2f}
💼 <b>Margin:</b> ${margin:.2f}
⚡ <b>Leverage:</b> x200

{pnl_emoji} <b>Unrealized P&L:</b> {pnl_sign}{pnl:.2f} USDT
📈 <b>ROI:</b> {pnl_sign}{roi:.2f}%
💳 <b>Balance:</b> ${balance:.2f}

⏰ <b>Entry Time:</b> {datetime.fromisoformat(position["entry_time"]).strftime("%H:%M:%S")}
            """.strip()
        
        self.send_message(message)
    
    def send_position_opened(self, position, current_price, trade_number=1, balance=0):
        """Send notification when position is opened"""
        side_emoji = "📈" if position["side"] == "long" else "📉"
        side_text = "LONG" if position["side"] == "long" else "SHORT"
        
        message = f"""
✅ <b>POSITION OPENED #{trade_number}</b>
{side_emoji} <b>{side_text} SOL/USDT</b> (10.0% of bank)

💰 <b>Entry Price:</b> ${position["entry_price"]:.2f}
💰 <b>Current Price:</b> ${current_price:.2f}
🎯 <b>Size:</b> {position["size_base"]:.4f} SOL
💼 <b>Notional:</b> ${position["notional"]:.2f}
💼 <b>Margin:</b> ${position["notional"]/200:.2f}
⚡ <b>Leverage:</b> x200
💳 <b>Balance:</b> ${balance:.2f}

⏰ <b>Entry Time:</b> {datetime.fromisoformat(position["entry_time"]).strftime("%H:%M:%S")}
        """.strip()
        
        self.send_message(message)
    
    def send_position_closed(self, trade, trade_number=1, balance=0):
        """Send notification when position is closed"""
        side_emoji = "📈" if trade["side"] == "long" else "📉"
        side_text = "LONG" if trade["side"] == "long" else "SHORT"
        
        # Calculate ROI
        margin = trade["notional"] / 200
        roi = (trade["pnl"] / margin) * 100 if margin > 0 else 0
        
        # PnL emoji and color
        pnl_emoji = "💚" if trade["pnl"] > 0 else "❤️"
        pnl_sign = "+" if trade["pnl"] > 0 else ""
        
        message = f"""
✅ <b>POSITION CLOSED #{trade_number}</b>
{side_emoji} <b>{side_text} SOL/USDT</b> (10.0% of bank)

💰 <b>Entry Price:</b> ${trade["entry_price"]:.2f}
💰 <b>Exit Price:</b> ${trade["exit_price"]:.2f}
🎯 <b>Size:</b> {trade["size_base"]:.4f} SOL
💼 <b>Notional:</b> ${trade["notional"]:.2f}
💼 <b>Margin:</b> ${margin:.2f}
⚡ <b>Leverage:</b> x200

{pnl_emoji} <b>P&L:</b> {pnl_sign}{trade["pnl"]:.2f} USDT
📈 <b>ROI:</b> {pnl_sign}{roi:.2f}%
💳 <b>Balance:</b> ${balance:.2f}

⏰ <b>Exit Time:</b> {datetime.fromisoformat(trade["time"]).strftime("%H:%M:%S")}
⏱️ <b>Duration:</b> {trade.get("duration", "N/A")}
        """.strip()
        
        self.send_message(message)
    
    def send_error(self, error_message):
        """Send error notification"""
        message = f"""
❌ <b>TRADING BOT ERROR</b>

🚨 <b>Error:</b> {error_message}
⏰ <b>Time:</b> {datetime.utcnow().strftime("%H:%M:%S UTC")}

Please check the bot status and logs.
        """.strip()
        
        self.send_message(message)
    
    # send_bot_status removed by user request
    
    # send_balance_update removed by user request
    
    def add_subscriber(self, chat_id):
        """Add a new subscriber"""
        chat_id_str = str(chat_id)
        if chat_id_str not in self.chat_ids:
            self.chat_ids.append(chat_id_str)
            logging.info(f"✅ Added new Telegram subscriber: {chat_id_str} (Total: {len(self.chat_ids)})")
            return True
        logging.info(f"ℹ️ User {chat_id_str} already subscribed")
        return False
    
    def is_owner(self, user_id):
        """Check if user is the bot owner - now allows all users"""
        return True  # Open access for all users
    
    def handle_message(self, message):
        """Handle incoming Telegram message"""
        try:
            user_id = message.get('from', {}).get('id')
            chat_id = message.get('chat', {}).get('id')
            text = message.get('text', '').strip()
            
            # Handle /start command - subscribe new users
            if text.lower() == '/start':
                is_new = self.add_subscriber(str(chat_id))
                self.send_welcome_message(chat_id, is_new)
                return True
            
            # Handle other commands
            if text.lower() == '/help':
                self.send_help_message(chat_id)
            elif text.lower() == '/status':
                self.send_bot_status_on_demand(chat_id)
            elif text.lower() == '/subscribe':
                is_new = self.add_subscriber(str(chat_id))
                if is_new:
                    self.send_message_to_chat(chat_id, "✅ You are now subscribed to trading notifications!")
                else:
                    self.send_message_to_chat(chat_id, "✅ You are already subscribed!")
            else:
                # Unknown command
                self.send_message_to_chat(chat_id, "Unknown command. Use /help to see available commands.")
                
            return True
            
        except Exception as e:
            logging.error(f"Error handling Telegram message: {e}")
            return False
    
    def send_welcome_message(self, chat_id, is_new_subscriber):
        """Send welcome message to new or returning users"""
        if is_new_subscriber:
            message = """
🎉 <b>Welcome to SOL/USDT Trading Bot!</b>

✅ You are now subscribed to real-time trading notifications!

<b>What you'll receive:</b>
• 📈 Position opened/closed alerts
• 💰 Profit/Loss updates
• 🚨 Important trading signals
• ⚡ Bot status changes

<b>Available commands:</b>
/status - Check current bot status
/help - Show all commands
/subscribe - Subscribe to notifications

🤖 <b>Strategy:</b> Parabolic SAR + MACD
💼 <b>Pair:</b> SOL/USDT x200
📊 <b>Mode:</b> Paper Trading

Let's make some profits! 🚀
            """.strip()
        else:
            message = """
👋 <b>Welcome back!</b>

You are already subscribed to trading notifications.

Use /help to see available commands.
            """.strip()
        self.send_message_to_chat(chat_id, message)
    
    def send_help_message(self, chat_id):
        """Send help message to all users"""
        message = """
🤖 <b>Trading Bot Commands</b>

/start - Subscribe to notifications
/status - Check bot status and balance
/subscribe - Subscribe to alerts
/help - Show this help message

📢 <b>This bot is open for everyone!</b>

<b>Notifications you'll receive:</b>
• 📈 Positions opened/closed
• 💰 P&L updates
• 🚨 Trading signals
• ⚡ Bot status updates

<b>Trading Info:</b>
• Strategy: Parabolic SAR + MACD
• Pair: SOL/USDT
• Leverage: x200
• Mode: Paper Trading
        """.strip()
        self.send_message_to_chat(chat_id, message)
    
    def send_bot_status_on_demand(self, chat_id):
        """Send bot status when requested by owner"""
        try:
            # Get status via API request to avoid circular imports
            import requests
            try:
                response = requests.get('http://localhost:5000/api/get_global_state', timeout=5)
                if response.status_code == 200:
                    state = response.json()
                    bot_running = state.get('bot_running', False)
                    balance = state.get('balance', 0)
                    in_position = state.get('in_position', False)
                    current_price = state.get('current_price', 0)
                else:
                    raise Exception("API request failed")
            except:
                # Fallback to direct import if API fails
                from trading_bot import state
                bot_running = False  # Can't get this from trading_bot state
                balance = state.get('balance', 0)
                in_position = state.get('in_position', False)
                current_price = 0
            
            status_emoji = "🟢" if bot_running else "🔴"
            position_emoji = "📊" if in_position else "💤"
            
            message = f"""
{status_emoji} <b>Bot Status:</b> {"Running" if bot_running else "Stopped"}
💰 <b>Balance:</b> ${balance:.2f}
{position_emoji} <b>Position:</b> {"Active" if in_position else "None"}
💱 <b>SOL Price:</b> ${current_price:.2f}
            """.strip()
            
            self.send_message_to_chat(chat_id, message)
            
        except Exception as e:
            error_msg = f"Error getting bot status: {str(e)}"
            self.send_message_to_chat(chat_id, error_msg)
            logging.error(error_msg)
    
    def send_message_to_chat(self, chat_id, message):
        """Send message to specific chat"""
        try:
            url = f"{self.base_url}/sendMessage"
            data = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            
            response = requests.post(url, data=data, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            logging.error(f"Failed to send message to chat {chat_id}: {e}")
            return False
    
    def get_bot_info(self):
        """Get bot username for subscription instructions"""
        if not self.bot_token:
            return None
        try:
            url = f"{self.base_url}/getMe"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            bot_info = response.json()
            return bot_info.get('result', {}).get('username')
        except Exception as e:
            logging.error(f"Failed to get bot info: {e}")
            return None
