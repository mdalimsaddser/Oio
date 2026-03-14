import requests
import time
import json
import threading
from datetime import datetime, timedelta
import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging

# টেলিগ্রাম বট কনফিগারেশন
BOT_TOKEN = "8781880934:AAGWsBhjIXM3G6VuXKfWdQrnsP9BdEhJnWo"
ADMIN_ID = 8563280306

# লগিং সেটআপ
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# টেলিগ্রাম বট ইনিশিয়ালাইজ
bot = telebot.TeleBot(BOT_TOKEN)

class OTPBot:
    def __init__(self):
        self.api_keys = []
        self.current_api_index = 0
        self.blocked_until = {}
        self.rate_limit_wait = 3
        self.config_file = "config.json"
        self.is_running = False
        self.current_task = None
        self.stats = {
            'total_sent': 0,
            'total_failed': 0,
            'total_blocked': 0,
            'start_time': None
        }
        self.load_config()
        self.load_blocked_numbers()
        
    def load_config(self):
        """Load API keys from config file"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    self.api_keys = config.get('api_keys', [])
                logger.info(f"✅ Loaded {len(self.api_keys)} API keys from config")
            except Exception as e:
                logger.error(f"Error loading config: {e}")
                self.api_keys = []
        else:
            self.save_config()
    
    def save_config(self):
        """Save API keys to config file"""
        config = {
            'api_keys': self.api_keys,
            'last_update': datetime.now().isoformat()
        }
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving config: {e}")
    
    def add_api_key(self, app_id, api_key, user_id=None):
        """Add new API key to the pool"""
        # Check if already exists
        for key in self.api_keys:
            if key['app_id'] == app_id and key['api_key'] == api_key:
                return False, "এই API key আগেই যোগ করা আছে!"
        
        new_key = {
            'app_id': app_id,
            'api_key': api_key,
            'added_on': datetime.now().isoformat(),
            'added_by': user_id,
            'is_active': True,
            'error_count': 0,
            'last_used': None
        }
        self.api_keys.append(new_key)
        self.save_config()
        return True, f"✅ API Key যোগ করা হয়েছে!\nমোট কী: {len(self.api_keys)}"
    
    def remove_api_key(self, index):
        """Remove API key by index"""
        if 0 <= index < len(self.api_keys):
            removed = self.api_keys.pop(index)
            self.save_config()
            return True, f"✅ API key {index+1} সরানো হয়েছে"
        return False, "❌ ভুল ইনডেক্স!"
    
    def get_current_api(self):
        """Get current active API key"""
        if not self.api_keys:
            return None, None
        
        # Try to find an active key
        for i in range(len(self.api_keys)):
            idx = (self.current_api_index + i) % len(self.api_keys)
            if self.api_keys[idx].get('is_active', True):
                self.current_api_index = idx
                return self.api_keys[idx]['app_id'], self.api_keys[idx]['api_key']
        
        return None, None
    
    def switch_to_next_api(self):
        """Switch to next available API key"""
        if not self.api_keys:
            return False
        
        # Mark current as problematic if not already
        if self.current_api_index < len(self.api_keys):
            self.api_keys[self.current_api_index]['error_count'] = self.api_keys[self.current_api_index].get('error_count', 0) + 1
            
            # Deactivate if too many errors
            if self.api_keys[self.current_api_index]['error_count'] >= 3:
                self.api_keys[self.current_api_index]['is_active'] = False
                logger.warning(f"API key {self.current_api_index + 1} deactivated due to multiple errors")
                self.notify_admin(f"⚠️ API key {self.current_api_index + 1} ডিঅ্যাক্টিভ করা হয়েছে\nকারণ: ৩ বার ত্রুটি")
        
        # Find next active key
        start_idx = (self.current_api_index + 1) % len(self.api_keys)
        for i in range(len(self.api_keys)):
            idx = (start_idx + i) % len(self.api_keys)
            if self.api_keys[idx].get('is_active', True):
                self.current_api_index = idx
                logger.info(f"Switched to API key {idx + 1}")
                self.notify_admin(f"🔄 সুইচ করা হয়েছে API key {idx + 1} তে")
                return True
        
        self.notify_admin("❌ কোনো একটিভ API key নেই!")
        return False
    
    def notify_admin(self, message):
        """Send notification to admin"""
        try:
            bot.send_message(ADMIN_ID, message)
        except Exception as e:
            logger.error(f"Failed to notify admin: {e}")
    
    def is_number_blocked(self, phone_number):
        """Check if number is temporarily blocked"""
        if phone_number in self.blocked_until:
            if datetime.now() < self.blocked_until[phone_number]:
                wait_time = (self.blocked_until[phone_number] - datetime.now()).seconds // 60
                return True, wait_time
            else:
                # Block time expired
                del self.blocked_until[phone_number]
        return False, 0
    
    def block_number(self, phone_number):
        """Block number for 1 hour"""
        block_until = datetime.now() + timedelta(hours=1)
        self.blocked_until[phone_number] = block_until
        self.stats['total_blocked'] += 1
        logger.info(f"Blocked {phone_number} until {block_until.strftime('%H:%M:%S')}")
        
        # Save blocked status
        self.save_blocked_numbers()
        return block_until
    
    def save_blocked_numbers(self):
        """Save blocked numbers to file"""
        blocked_data = {}
        for num, until in self.blocked_until.items():
            blocked_data[num] = until.isoformat()
        
        try:
            with open('blocked_numbers.json', 'w') as f:
                json.dump(blocked_data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving blocked numbers: {e}")
    
    def load_blocked_numbers(self):
        """Load blocked numbers from file"""
        if os.path.exists('blocked_numbers.json'):
            try:
                with open('blocked_numbers.json', 'r') as f:
                    data = json.load(f)
                    for num, until_str in data.items():
                        until = datetime.fromisoformat(until_str)
                        if datetime.now() < until:
                            self.blocked_until[num] = until
                logger.info(f"Loaded {len(self.blocked_until)} blocked numbers")
            except Exception as e:
                logger.error(f"Error loading blocked numbers: {e}")
    
    def send_otp(self, phone_number, use_whatsapp=False, max_retries=3):
        """Send OTP with automatic API switching and block handling"""
        
        # Check if number is blocked
        is_blocked, wait_time = self.is_number_blocked(phone_number)
        if is_blocked:
            return False, f"ব্লক করা আছে। আর {wait_time} মিনিট বাকি"
        
        # Format phone number
        if not phone_number.startswith('+'):
            phone_number = '+' + phone_number.lstrip('0')
        
        for attempt in range(max_retries):
            # Get current API credentials
            app_id, api_key = self.get_current_api()
            
            if not app_id or not api_key:
                return False, "❌ কোনো API key নেই!"
            
            headers = {
                "x-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            
            url = f"https://verification.didit.me/v3/phone/send/"
            
            payload = {
                "phone_number": phone_number,
                "application_id": app_id,
                "options": {
                    "code_size": 6,
                    "locale": "en",
                    "preferred_channel": "whatsapp" if use_whatsapp else "sms"
                }
            }
            
            try:
                logger.info(f"Sending to {phone_number} (Attempt {attempt + 1}/{max_retries})")
                response = requests.post(url, json=payload, headers=headers, timeout=30)
                
                # Update last used time
                if self.current_api_index < len(self.api_keys):
                    self.api_keys[self.current_api_index]['last_used'] = datetime.now().isoformat()
                
                # Check response
                if response.status_code == 200:
                    data = response.json()
                    
                    # Check for blocked status in response
                    if data.get('status') == 'Blocked' and data.get('reason') == 'suspicious':
                        block_until = self.block_number(phone_number)
                        return False, f"🚫 সন্দেহজনক! ১ ঘন্টার জন্য ব্লক"
                    
                    self.stats['total_sent'] += 1
                    return True, "✅ OTP পাঠানো হয়েছে"
                    
                elif response.status_code == 402 or "credits" in response.text.lower():
                    logger.warning("Insufficient credits! Switching API key...")
                    if self.switch_to_next_api():
                        continue
                    else:
                        return False, "❌ কোনো API key তে ক্রেডিট নেই!"
                        
                elif response.status_code == 403:
                    try:
                        error_data = response.json()
                        if error_data.get('status') == 'Blocked' and error_data.get('reason') == 'suspicious':
                            self.block_number(phone_number)
                            return False, "🚫 সন্দেহজনক! ১ ঘন্টার জন্য ব্লক"
                    except:
                        pass
                    
                    if self.switch_to_next_api():
                        continue
                    else:
                        return False, "❌ এক্সেস নাই!"
                        
                else:
                    logger.error(f"Error {response.status_code}")
                    try:
                        error_data = response.json()
                        if error_data.get('status') == 'Blocked' and error_data.get('reason') == 'suspicious':
                            self.block_number(phone_number)
                            return False, "🚫 সন্দেহজনক! ১ ঘন্টার জন্য ব্লक"
                    except:
                        pass
                    
                    if attempt < max_retries - 1:
                        if self.switch_to_next_api():
                            time.sleep(2)
                            continue
                    
                    self.stats['total_failed'] += 1
                    return False, f"❌ ত্রুটি: {response.status_code}"
                    
            except Exception as e:
                logger.error(f"Error: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                self.stats['total_failed'] += 1
                return False, f"❌ এরর: {str(e)[:50]}"
        
        return False, "❌ সর্বোচ্চ চেষ্টা ব্যর্থ"
    
    def process_file(self, file_path, use_whatsapp, chat_id, message_id=None):
        """Process phone numbers from file"""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                phone_numbers = [line.strip() for line in f if line.strip()]
            
            if not phone_numbers:
                bot.send_message(chat_id, "❌ ফাইলটি খালি!")
                return
            
            # Send initial status
            status_msg = bot.send_message(chat_id, 
                f"🚀 OTP পাঠানো শুরু...\n"
                f"📊 মোট নাম্বার: {len(phone_numbers)}\n"
                f"📱 চ্যানেল: {'WhatsApp' if use_whatsapp else 'SMS'}")
            
            success = 0
            failed = 0
            blocked = 0
            skipped = 0
            
            self.stats['start_time'] = datetime.now()
            
            for i, phone in enumerate(phone_numbers, 1):
                # Check if number is blocked
                is_blocked, wait_time = self.is_number_blocked(phone)
                if is_blocked:
                    skipped += 1
                    continue
                
                # Send OTP
                result, message = self.send_otp(phone, use_whatsapp)
                
                if result:
                    success += 1
                else:
                    if "ব্লক" in message or "সন্দেহজনক" in message:
                        blocked += 1
                    else:
                        failed += 1
                
                # Update status every 5 numbers
                if i % 5 == 0 or i == len(phone_numbers):
                    progress = (i / len(phone_numbers)) * 100
                    try:
                        bot.edit_message_text(
                            f"🚀 অগ্রগতি: {i}/{len(phone_numbers)} ({progress:.1f}%)\n"
                            f"✅ সফল: {success}\n"
                            f"❌ ব্যর্থ: {failed}\n"
                            f"🚫 ব্লক: {blocked}\n"
                            f"⏸️ স্কিপ: {skipped}\n"
                            f"⏱️ চালু আছে...",
                            chat_id, status_msg.message_id
                        )
                    except:
                        pass
                
                # Rate limiting
                if i < len(phone_numbers):
                    time.sleep(self.rate_limit_wait)
            
            # Final report
            time_taken = (datetime.now() - self.stats['start_time']).seconds
            report = (
                f"✅ **কাজ শেষ!**\n\n"
                f"📊 **রিপোর্ট:**\n"
                f"├ মোট নাম্বার: {len(phone_numbers)}\n"
                f"├ ✅ সফল: {success}\n"
                f"├ ❌ ব্যর্থ: {failed}\n"
                f"├ 🚫 ব্লক: {blocked}\n"
                f"├ ⏸️ স্কিপ: {skipped}\n"
                f"└ ⏱️ সময়: {time_taken} সেকেন্ড\n\n"
                f"📱 চ্যানেল: {'WhatsApp' if use_whatsapp else 'SMS'}"
            )
            
            bot.send_message(chat_id, report, parse_mode='Markdown')
            
        except Exception as e:
            bot.send_message(chat_id, f"❌ এরর: {str(e)}")
        finally:
            self.is_running = False

# টেলিগ্রাম কমান্ড হ্যান্ডলার
@bot.message_handler(commands=['start'])
def start_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ আপনি অ্যাডমিন নন!")
        return
    
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📊 স্ট্যাটাস", callback_data="status"),
        InlineKeyboardButton("🔑 API Keys", callback_data="list_apis"),
        InlineKeyboardButton("📁 ফাইল পাঠান", callback_data="send_file"),
        InlineKeyboardButton("🚫 ব্লক লিস্ট", callback_data="blocked_list"),
        InlineKeyboardButton("⚙️ সেটিংস", callback_data="settings"),
        InlineKeyboardButton("🛑 স্টপ", callback_data="stop_task")
    )
    
    bot.send_message(
        message.chat.id,
        "🤖 **OTP বট কন্ট্রোল প্যানেল**\n\n"
        "নিচের বাটন ব্যবহার করুন:",
        reply_markup=markup,
        parse_mode='Markdown'
    )

@bot.message_handler(commands=['add_api'])
def add_api_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        parts = message.text.split()
        if len(parts) == 3:
            app_id = parts[1]
            api_key = parts[2]
            success, msg = otp_bot.add_api_key(app_id, api_key, message.from_user.id)
            bot.reply_to(message, msg)
        else:
            bot.reply_to(message, "❌ ব্যবহার: /add_api APP_ID API_KEY")
    except Exception as e:
        bot.reply_to(message, f"❌ এরর: {str(e)}")

@bot.message_handler(commands=['remove_api'])
def remove_api_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        index = int(message.text.split()[1]) - 1
        success, msg = otp_bot.remove_api_key(index)
        bot.reply_to(message, msg)
    except:
        bot.reply_to(message, "❌ ব্যবহার: /remove_api INDEX_NUMBER")

@bot.message_handler(commands=['status'])
def status_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    show_status(message.chat.id)

@bot.message_handler(content_types=['document'])
def handle_document(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    if otp_bot.is_running:
        bot.reply_to(message, "❌ আগের টাস্ক চলছে! /stop দিয়ে বন্ধ করুন")
        return
    
    try:
        # Download file
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Save file
        file_path = f"telegram_{message.document.file_name}"
        with open(file_path, 'wb') as f:
            f.write(downloaded_file)
        
        # Ask for channel
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("📱 SMS", callback_data=f"channel_sms_{file_path}"),
            InlineKeyboardButton("💬 WhatsApp", callback_data=f"channel_wa_{file_path}")
        )
        
        bot.reply_to(
            message, 
            f"✅ ফাইল পাওয়া গেছে: {message.document.file_name}\n"
            "চ্যানেল সিলেক্ট করুন:",
            reply_markup=markup
        )
        
    except Exception as e:
        bot.reply_to(message, f"❌ এরর: {str(e)}")

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if call.from_user.id != ADMIN_ID:
        return
    
    if call.data == "status":
        show_status(call.message.chat.id)
    
    elif call.data == "list_apis":
        show_api_list(call.message.chat.id)
    
    elif call.data == "send_file":
        bot.send_message(
            call.message.chat.id,
            "📁 যেকোনো টেক্সট ফাইল আপলোড করুন\n"
            "ফরম্যাট: প্রতিটি লাইনে এক একটি নাম্বার"
        )
    
    elif call.data == "blocked_list":
        show_blocked_list(call.message.chat.id)
    
    elif call.data == "settings":
        show_settings(call.message.chat.id)
    
    elif call.data == "stop_task":
        if otp_bot.is_running:
            otp_bot.is_running = False
            bot.answer_callback_query(call.id, "✅ টাস্ক বন্ধ করা হয়েছে")
            bot.send_message(call.message.chat.id, "🛑 টাস্ক বন্ধ করা হয়েছে")
        else:
            bot.answer_callback_query(call.id, "❌ কোন টাস্ক চলছে না")
    
    elif call.data.startswith("channel_"):
        parts = call.data.split('_')
        channel = parts[1]
        file_path = '_'.join(parts[2:])
        
        use_whatsapp = (channel == "wa")
        
        if not otp_bot.is_running:
            otp_bot.is_running = True
            thread = threading.Thread(
                target=otp_bot.process_file,
                args=(file_path, use_whatsapp, call.message.chat.id, call.message.message_id)
            )
            thread.daemon = True
            thread.start()
            bot.answer_callback_query(call.id, "🚀 শুরু হচ্ছে...")
        else:
            bot.answer_callback_query(call.id, "❌ আগের টাস্ক শেষ হোক")

def show_status(chat_id):
    status_text = (
        f"📊 **বর্তমান স্ট্যাটাস**\n\n"
        f"🔑 API Keys: {len(otp_bot.api_keys)}\n"
        f"✅ একটিভ Keys: {sum(1 for k in otp_bot.api_keys if k.get('is_active', True))}\n"
        f"🚫 ব্লক করা নাম্বার: {len(otp_bot.blocked_until)}\n"
        f"📊 টোটাল সেন্ট: {otp_bot.stats['total_sent']}\n"
        f"❌ টোটাল ফেইল: {otp_bot.stats['total_failed']}\n"
        f"🚫 টোটাল ব্লক: {otp_bot.stats['total_blocked']}\n"
        f"⚙️ চালু আছে: {'হ্যাঁ' if otp_bot.is_running else 'না'}\n"
    )
    
    if otp_bot.current_api_index < len(otp_bot.api_keys):
        status_text += f"\nবর্তমান API: {otp_bot.current_api_index + 1}"
    
    bot.send_message(chat_id, status_text, parse_mode='Markdown')

def show_api_list(chat_id):
    if not otp_bot.api_keys:
        bot.send_message(chat_id, "❌ কোন API key নেই!\n/add_api কমান্ড ব্যবহার করুন")
        return
    
    text = "🔑 **API Keys লিস্ট:**\n\n"
    for i, key in enumerate(otp_bot.api_keys, 1):
        status = "✅" if key.get('is_active', True) else "❌"
        errors = key.get('error_count', 0)
        text += f"{status} {i}. {key['app_id'][:8]}...\n"
        text += f"   ├ Errors: {errors}\n"
        text += f"   └ Added: {key['added_on'][:10]}\n"
    
    text += f"\nমোট: {len(otp_bot.api_keys)} টি"
    bot.send_message(chat_id, text)

def show_blocked_list(chat_id):
    if not otp_bot.blocked_until:
        bot.send_message(chat_id, "✅ কোন ব্লক করা নাম্বার নেই")
        return
    
    text = "🚫 **ব্লক করা নাম্বার:**\n\n"
    for number, until in list(otp_bot.blocked_until.items())[:10]:  # Show first 10
        remaining = (until - datetime.now()).seconds // 60
        text += f"📞 {number}\n"
        text += f"   └ আনব্লক হবে: {remaining} মিনিট পর\n"
    
    if len(otp_bot.blocked_until) > 10:
        text += f"\n... এবং আরও {len(otp_bot.blocked_until) - 10} টি"
    
    bot.send_message(chat_id, text, parse_mode='Markdown')

def show_settings(chat_id):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("⏱️ Delay: 3s", callback_data="delay_3"),
        InlineKeyboardButton("⏱️ Delay: 5s", callback_data="delay_5"),
        InlineKeyboardButton("⏱️ Delay: 10s", callback_data="delay_10")
    )
    
    bot.send_message(
        chat_id,
        f"⚙️ **সেটিংস**\n\n"
        f"বর্তমান Delay: {otp_bot.rate_limit_wait} সেকেন্ড",
        reply_markup=markup,
        parse_mode='Markdown'
    )

# মনিটরিং থ্রেড
def monitor_blocked_numbers():
    while True:
        current_time = datetime.now()
        to_remove = []
        
        for number, block_until in otp_bot.blocked_until.items():
            if current_time >= block_until:
                to_remove.append(number)
        
        for number in to_remove:
            del otp_bot.blocked_until[number]
            logger.info(f"Number {number} unblocked automatically")
        
        if to_remove:
            otp_bot.save_blocked_numbers()
        
        time.sleep(60)

# মেইন এক্সিকিউশন
if __name__ == "__main__":
    print("""
    ╔══════════════════════════════════╗
    ║     TELEGRAM OTP BOT v3.0        ║
    ║   Telegram Control + Auto Switch  ║
    ╚══════════════════════════════════╝
    """)
    
    # Initialize bot
    otp_bot = OTPBot()
    
    # Start monitor thread
    monitor_thread = threading.Thread(target=monitor_blocked_numbers, daemon=True)
    monitor_thread.start()
    
    # Notify admin that bot is running
    try:
        bot.send_message(ADMIN_ID, "🤖 বট চালু হয়েছে!\n/start কমান্ড দিন")
    except:
        print("⚠️ Admin notification failed")
    
    # Start telegram bot
    print("✅ Telegram bot is running...")
    bot.infinity_polling()
