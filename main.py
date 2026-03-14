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
        self.blocked_numbers_file = "blocked_numbers.json"
        self.rate_limit_wait = 3
        self.config_file = "config.json"
        self.is_running = False
        self.stop_requested = False
        
        # ব্লক হ্যান্ডলিং এর জন্য ভেরিয়েবল
        self.is_paused = False
        self.pause_start_time = None
        self.pause_duration = 3600  # 1 ঘন্টা = 3600 সেকেন্ড
        self.pause_end_time = None
        self.pause_reason = None
        self.blocked_number = None
        
        # প্রসেসিং স্টেট
        self.pending_numbers = []
        self.current_processing_index = 0
        self.use_whatsapp = False
        self.current_chat_id = None
        self.status_message = None
        self.file_path = None
        
        # স্ট্যাটিসটিক্স
        self.stats = {
            'total_sent': 0,
            'total_failed': 0,
            'total_blocked': 0,
            'total_pauses': 0,
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
        
        if self.current_api_index < len(self.api_keys):
            self.api_keys[self.current_api_index]['error_count'] = self.api_keys[self.current_api_index].get('error_count', 0) + 1
            
            if self.api_keys[self.current_api_index]['error_count'] >= 3:
                self.api_keys[self.current_api_index]['is_active'] = False
                logger.warning(f"API key {self.current_api_index + 1} deactivated due to multiple errors")
                self.notify_admin(f"⚠️ API key {self.current_api_index + 1} ডিঅ্যাক্টিভ করা হয়েছে\nকারণ: ৩ বার ত্রুটি")
        
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
                del self.blocked_until[phone_number]
                self.save_blocked_numbers()
                logger.info(f"Number {phone_number} automatically unblocked after 1 hour")
        return False, 0
    
    def block_number(self, phone_number):
        """Block number for 1 hour"""
        block_until = datetime.now() + timedelta(hours=1)
        self.blocked_until[phone_number] = block_until
        self.stats['total_blocked'] += 1
        logger.info(f"Blocked {phone_number} until {block_until.strftime('%H:%M:%S')}")
        
        self.save_blocked_numbers()
        self.notify_admin(f"🚫 নাম্বার ব্লক: {phone_number}\n📅 আনব্লক হবে: {block_until.strftime('%H:%M:%S')}")
        
        return block_until
    
    def save_blocked_numbers(self):
        """Save blocked numbers to file"""
        blocked_data = {}
        for num, until in self.blocked_until.items():
            blocked_data[num] = until.isoformat()
        
        try:
            with open(self.blocked_numbers_file, 'w') as f:
                json.dump(blocked_data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving blocked numbers: {e}")
    
    def load_blocked_numbers(self):
        """Load blocked numbers from file"""
        if os.path.exists(self.blocked_numbers_file):
            try:
                with open(self.blocked_numbers_file, 'r') as f:
                    data = json.load(f)
                    current_time = datetime.now()
                    for num, until_str in data.items():
                        until = datetime.fromisoformat(until_str)
                        if current_time < until:
                            self.blocked_until[num] = until
                        else:
                            logger.info(f"Number {num} already unblocked (expired)")
                logger.info(f"Loaded {len(self.blocked_until)} blocked numbers")
            except Exception as e:
                logger.error(f"Error loading blocked numbers: {e}")
    
    def check_and_unblock_numbers(self):
        """Check and unblock expired numbers"""
        current_time = datetime.now()
        unblocked = []
        
        for number, block_until in list(self.blocked_until.items()):
            if current_time >= block_until:
                del self.blocked_until[number]
                unblocked.append(number)
                logger.info(f"Number {number} automatically unblocked")
        
        if unblocked:
            self.save_blocked_numbers()
            self.notify_admin(f"✅ {len(unblocked)} টি নাম্বার আনব্লক করা হয়েছে")
        
        return unblocked
    
    def pause_processing(self, reason, blocked_number):
        """পুরো প্রসেসিং ১ ঘন্টার জন্য পজ করে দেয়"""
        self.is_paused = True
        self.pause_start_time = datetime.now()
        self.pause_end_time = self.pause_start_time + timedelta(hours=1)
        self.pause_reason = reason
        self.blocked_number = blocked_number
        self.stats['total_pauses'] += 1
        
        logger.info(f"⚠️ Processing PAUSED for 1 hour due to: {reason}")
        
        # এডমিনকে নোটিফাই
        self.notify_admin(
            f"⏸️ **OTP সেন্ডিং পজ**\n\n"
            f"কারণ: {reason}\n"
            f"ব্লক নাম্বার: {blocked_number}\n"
            f"পজ শুরু: {self.pause_start_time.strftime('%H:%M:%S')}\n"
            f"পজ শেষ: {self.pause_end_time.strftime('%H:%M:%S')}\n\n"
            f"১ ঘন্টা পর আবার শুরু হবে"
        )
        
        # স্ট্যাটাস আপডেট
        self.update_status_message(
            f"⏸️ **পজ | {reason}**\n"
            f"আর {self.get_pause_remaining()} মিনিট বাকি"
        )
    
    def resume_processing(self):
        """পজ শেষে আবার প্রসেসিং শুরু করে"""
        self.is_paused = False
        self.pause_start_time = None
        self.pause_end_time = None
        self.pause_reason = None
        self.blocked_number = None
        
        logger.info("▶️ Processing RESUMED after 1 hour pause")
        
        # এডমিনকে নোটিফাই
        self.notify_admin(
            f"▶️ **OTP সেন্ডিং আবার শুরু**\n\n"
            f"১ ঘন্টা পজ শেষ\n"
            f"যেখানে বন্ধ ছিল সেখান থেকে শুরু হচ্ছে..."
        )
        
        # স্ট্যাটাস আপডেট
        self.update_status_message("▶️ পজ শেষ, আবার শুরু হচ্ছে...")
    
    def get_pause_remaining(self):
        """পজ শেষ হতে কত মিনিট বাকি তা রিটার্ন করে"""
        if not self.is_paused or not self.pause_end_time:
            return 0
        
        remaining = (self.pause_end_time - datetime.now()).seconds
        if remaining <= 0:
            self.resume_processing()
            return 0
        
        return remaining // 60
    
    def update_status_message(self, additional_text=""):
        """স্ট্যাটাস মেসেজ আপডেট করে"""
        if not self.status_message or not self.current_chat_id:
            return
        
        try:
            total = len(self.pending_numbers)
            current = self.current_processing_index
            progress = (current / total) * 100 if total > 0 else 0
            
            # পজ স্ট্যাটাস
            pause_info = ""
            if self.is_paused:
                remaining = self.get_pause_remaining()
                pause_info = f"\n⏸️ **পজ** | {remaining} মিনিট বাকি\nব্লক নাম্বার: {self.blocked_number}"
            
            message_text = (
                f"🚀 **OTP বট v4.0**\n"
                f"{'='*30}\n"
                f"📊 অগ্রগতি: {current}/{total} ({progress:.1f}%)\n"
                f"├ ✅ সফল: {self.stats['total_sent']}\n"
                f"├ ❌ ব্যর্থ: {self.stats['total_failed']}\n"
                f"├ 🚫 ব্লক: {self.stats['total_blocked']}\n"
                f"├ ⏸️ পজ কাউন্ট: {self.stats['total_pauses']}\n"
                f"└ 📱 চ্যানেল: {'WhatsApp' if self.use_whatsapp else 'SMS'}\n"
                f"{pause_info}\n"
                f"{additional_text}\n"
                f"{'='*30}\n"
                f"🛑 বন্ধ করতে /stop কমান্ড দিন"
            )
            
            bot.edit_message_text(
                message_text,
                self.current_chat_id,
                self.status_message.message_id,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Error updating status: {e}")
    
    def send_otp(self, phone_number, use_whatsapp=False, max_retries=3):
        """Send OTP with automatic API switching and block handling"""
        
        # চেক করি নাম্বার ব্লক কিনা
        is_blocked, wait_time = self.is_number_blocked(phone_number)
        if is_blocked:
            return False, "blocked", f"ব্লক করা আছে। আর {wait_time} মিনিট বাকি"
        
        # ফরম্যাট ফোন নাম্বার
        if not phone_number.startswith('+'):
            phone_number = '+' + phone_number.lstrip('0')
        
        for attempt in range(max_retries):
            app_id, api_key = self.get_current_api()
            
            if not app_id or not api_key:
                return False, "error", "❌ কোনো API key নেই!"
            
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
                
                if self.current_api_index < len(self.api_keys):
                    self.api_keys[self.current_api_index]['last_used'] = datetime.now().isoformat()
                
                # সফল
                if response.status_code == 200:
                    data = response.json()
                    
                    if data.get('status') == 'Blocked' and data.get('reason') == 'suspicious':
                        self.block_number(phone_number)
                        return False, "block", f"🚫 সন্দেহজনক! ১ ঘন্টার জন্য ব্লক"
                    
                    self.stats['total_sent'] += 1
                    return True, "success", "✅ OTP পাঠানো হয়েছে"
                
                # ক্রেডিট নেই
                elif response.status_code == 402 or "credits" in response.text.lower():
                    logger.warning("Insufficient credits! Switching API key...")
                    if self.switch_to_next_api():
                        continue
                    else:
                        return False, "error", "❌ কোনো API key তে ক্রেডিট নেই!"
                
                # ব্লক বা এক্সেস সমস্যা
                elif response.status_code == 403:
                    try:
                        error_data = response.json()
                        if error_data.get('status') == 'Blocked' and error_data.get('reason') == 'suspicious':
                            self.block_number(phone_number)
                            return False, "block", f"🚫 সন্দেহজনক! ১ ঘন্টার জন্য ব্লক"
                    except:
                        pass
                    
                    if self.switch_to_next_api():
                        continue
                    else:
                        return False, "error", "❌ এক্সেস নাই!"
                
                # অন্য ত্রুটি
                else:
                    logger.error(f"Error {response.status_code}")
                    try:
                        error_data = response.json()
                        if error_data.get('status') == 'Blocked' and error_data.get('reason') == 'suspicious':
                            self.block_number(phone_number)
                            return False, "block", f"🚫 সন্দেহজনক! ১ ঘন্টার জন্য ব্লক"
                    except:
                        pass
                    
                    if attempt < max_retries - 1:
                        if self.switch_to_next_api():
                            time.sleep(2)
                            continue
                    
                    self.stats['total_failed'] += 1
                    return False, "error", f"❌ ত্রুটি: {response.status_code}"
                    
            except Exception as e:
                logger.error(f"Error: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                self.stats['total_failed'] += 1
                return False, "error", f"❌ এরর: {str(e)[:50]}"
        
        return False, "error", "❌ সর্বোচ্চ চেষ্টা ব্যর্থ"
    
    def process_file_24x7(self, file_path, use_whatsapp, chat_id):
        """২৪/৭ প্রসেসিং - ব্লক হলে ১ ঘন্টা পজ, তারপর আবার শুরু"""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                self.pending_numbers = [line.strip() for line in f if line.strip()]
            
            if not self.pending_numbers:
                bot.send_message(chat_id, "❌ ফাইলটি খালি!")
                return
            
            # সেভ করা
            self.file_path = file_path
            self.use_whatsapp = use_whatsapp
            self.current_chat_id = chat_id
            self.stop_requested = False
            self.is_paused = False
            
            # স্ট্যাটাস মেসেজ
            self.status_message = bot.send_message(chat_id, 
                f"🚀 **২৪/৭ OTP বট শুরু**\n\n"
                f"📊 মোট নাম্বার: {len(self.pending_numbers)}\n"
                f"📱 চ্যানেল: {'WhatsApp' if use_whatsapp else 'SMS'}\n"
                f"⏸️ ব্লক হলে ১ ঘন্টা পজ হবে\n"
                f"🔄 পজ শেষে আবার শুরু হবে\n"
                f"🛑 বন্ধ করতে /stop কমান্ড দিন",
                parse_mode='Markdown')
            
            self.stats['start_time'] = datetime.now()
            self.current_processing_index = 0
            
            # ২৪/৭ লুপ - যতক্ষণ না স্টপ কমান্ড দেয়
            while not self.stop_requested:
                
                # চেক করি পজ করা আছে কিনা
                if self.is_paused:
                    # পজ শেষ হয়েছে কিনা চেক
                    if datetime.now() >= self.pause_end_time:
                        self.resume_processing()
                    else:
                        # পজ অবস্থায় wait করি
                        remaining = self.get_pause_remaining()
                        self.update_status_message(f"⏸️ পজ | {remaining} মিনিট বাকি")
                        time.sleep(60)  # ১ মিনিট পর চেক
                        continue
                
                # সব নাম্বার শেষ হয়েছে কিনা চেক
                if self.current_processing_index >= len(self.pending_numbers):
                    # সব শেষ - আবার শুরু থেকে করব?
                    self.current_processing_index = 0
                    logger.info("🔄 All numbers processed, starting again from beginning")
                    self.notify_admin("🔄 সব নাম্বার প্রসেস হয়েছে, আবার শুরু থেকে শুরু হচ্ছে...")
                
                # বর্তমান নাম্বার নিই
                phone = self.pending_numbers[self.current_processing_index]
                
                # চেক করি নাম্বার ব্লক কিনা
                is_blocked, wait_time = self.is_number_blocked(phone)
                if is_blocked:
                    self.current_processing_index += 1
                    continue
                
                # OTP পাঠাই
                result, result_type, message = self.send_otp(phone, use_whatsapp)
                
                if result:
                    self.current_processing_index += 1
                else:
                    if result_type == "block":
                        # ব্লক - পুরো প্রসেস ১ ঘন্টা পজ
                        self.pause_processing(f"নাম্বার ব্লক: {phone}", phone)
                        # নাম্বারটা ব্লক লিস্টে আছে, তাই ইন্ডেক্স বাড়াব না
                        # পরবর্তী নাম্বার থেকে শুরু হবে যখন আবার চালু হবে
                    else:
                        # অন্য এরর - পরবর্তী নাম্বারে যাই
                        self.current_processing_index += 1
                
                # স্ট্যাটাস আপডেট (প্রতি ৫ নাম্বারে)
                if self.current_processing_index % 5 == 0:
                    self.update_status_message()
                
                # রেট লিমিট - পজ না থাকলে
                if not self.is_paused:
                    time.sleep(self.rate_limit_wait)
            
            # স্টপ কমান্ড দেওয়া হয়েছে
            bot.send_message(chat_id, 
                f"🛑 **প্রসেসিং বন্ধ করা হয়েছে**\n\n"
                f"শেষ অবস্থা: {self.current_processing_index}/{len(self.pending_numbers)}\n"
                f"মোট সফল: {self.stats['total_sent']}\n"
                f"মোট পজ: {self.stats['total_pauses']}")
            
        except Exception as e:
            bot.send_message(chat_id, f"❌ এরর: {str(e)}")
        finally:
            self.is_running = False
            self.stop_requested = False
            self.is_paused = False
            self.status_message = None
            # ফাইল ডিলিট না করে রাখি, আবার শুরু করতে পারবে
            
    def stop_processing(self):
        """Stop current processing gracefully"""
        if self.is_running:
            self.stop_requested = True
            self.notify_admin("🛑 প্রসেসিং বন্ধ করা হচ্ছে...")
            return True
        return False

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
        InlineKeyboardButton("📁 ফাইল আপলোড", callback_data="send_file"),
        InlineKeyboardButton("🚫 ব্লক লিস্ট", callback_data="blocked_list"),
        InlineKeyboardButton("⚙️ সেটিংস", callback_data="settings"),
        InlineKeyboardButton("🛑 স্টপ", callback_data="stop_task")
    )
    
    bot.send_message(
        message.chat.id,
        "🤖 **OTP বট v4.0 - ২৪/৭ মোড**\n\n"
        "**বৈশিষ্ট্য:**\n"
        "• কোন নাম্বার ব্লক হলে সাথে সাথে স্টপ\n"
        "• ১ ঘন্টার টাইমার শুরু\n"
        "• ১ ঘন্টা পর যেখানে বন্ধ ছিল সেখান থেকে শুরু\n"
        "• ২৪/৭ চলতে থাকবে যতক্ষণ না স্টপ দেন\n\n"
        "নিচের বাটন ব্যবহার করুন:",
        reply_markup=markup,
        parse_mode='Markdown'
    )

@bot.message_handler(commands=['stop'])
def stop_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    if otp_bot.stop_processing():
        bot.reply_to(message, "🛑 প্রসেসিং বন্ধ করা হচ্ছে...")
    else:
        bot.reply_to(message, "❌ কোন প্রসেসিং চলছে না!")

@bot.message_handler(commands=['status'])
def status_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    show_status(message.chat.id)

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

@bot.message_handler(content_types=['document'])
def handle_document(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    if otp_bot.is_running:
        bot.reply_to(message, "❌ আগের টাস্ক চলছে! /stop দিয়ে বন্ধ করুন")
        return
    
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        file_path = f"24x7_{message.document.file_name}"
        with open(file_path, 'wb') as f:
            f.write(downloaded_file)
        
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("📱 SMS (২৪/৭)", callback_data=f"247_sms_{file_path}"),
            InlineKeyboardButton("💬 WhatsApp (২৪/৭)", callback_data=f"247_wa_{file_path}")
        )
        
        bot.reply_to(
            message, 
            f"✅ ফাইল পাওয়া গেছে: {message.document.file_name}\n"
            "**২৪/৭ মোড সিলেক্ট করুন:**\n"
            "• ব্লক হলে ১ ঘন্টা পজ\n"
            "• পজ শেষে আবার শুরু",
            reply_markup=markup,
            parse_mode='Markdown'
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
            "ফরম্যাট: প্রতিটি লাইনে এক একটি নাম্বার\n\n"
            "⚠️ **২৪/৭ মোড:**\n"
            "• ব্লক হলে ১ ঘন্টা পজ\n"
            "• পজ শেষে আবার শুরু\n"
            "• স্টপ না দেওয়া পর্যন্ত চলবে",
            parse_mode='Markdown'
        )
    
    elif call.data == "blocked_list":
        show_blocked_list(call.message.chat.id)
    
    elif call.data == "settings":
        show_settings(call.message.chat.id)
    
    elif call.data == "stop_task":
        if otp_bot.stop_processing():
            bot.answer_callback_query(call.id, "🛑 বন্ধ করা হচ্ছে...")
        else:
            bot.answer_callback_query(call.id, "❌ কোন টাস্ক চলছে না")
    
    elif call.data.startswith("247_"):
        parts = call.data.split('_')
        channel = parts[1]
        file_path = '_'.join(parts[2:])
        
        use_whatsapp = (channel == "wa")
        
        if not otp_bot.is_running:
            otp_bot.is_running = True
            thread = threading.Thread(
                target=otp_bot.process_file_24x7,
                args=(file_path, use_whatsapp, call.message.chat.id)
            )
            thread.daemon = True
            thread.start()
            bot.answer_callback_query(call.id, "🚀 ২৪/৭ মোড শুরু হচ্ছে...")
        else:
            bot.answer_callback_query(call.id, "❌ আগের টাস্ক চলছে!")

def show_status(chat_id):
    """Show current status"""
    unblocked = otp_bot.check_and_unblock_numbers()
    
    status_text = (
        f"📊 **বর্তমান স্ট্যাটাস**\n\n"
        f"🔑 API Keys: {len(otp_bot.api_keys)}\n"
        f"✅ একটিভ Keys: {sum(1 for k in otp_bot.api_keys if k.get('is_active', True))}\n"
        f"🚫 ব্লক করা নাম্বার: {len(otp_bot.blocked_until)}\n"
        f"📊 টোটাল সেন্ট: {otp_bot.stats['total_sent']}\n"
        f"❌ টোটাল ফেইল: {otp_bot.stats['total_failed']}\n"
        f"🚫 টোটাল ব্লক: {otp_bot.stats['total_blocked']}\n"
        f"⏸️ টোটাল পজ: {otp_bot.stats['total_pauses']}\n"
        f"⚙️ চলছে: {'হ্যাঁ' if otp_bot.is_running else 'না'}\n"
    )
    
    if otp_bot.is_running:
        status_text += f"\n📌 চলমান অবস্থা:\n"
        status_text += f"├ প্রসেসড: {otp_bot.current_processing_index}/{len(otp_bot.pending_numbers)}\n"
        if otp_bot.is_paused:
            remaining = otp_bot.get_pause_remaining()
            status_text += f"├ ⏸️ পজ: {remaining} মিনিট বাকি\n"
            status_text += f"├ কারণ: {otp_bot.pause_reason}\n"
            status_text += f"└ ব্লক নাম্বার: {otp_bot.blocked_number}"
        else:
            status_text += f"└ ⏱️ চলছে..."
    
    if otp_bot.current_api_index < len(otp_bot.api_keys):
        status_text += f"\n\nবর্তমান API: {otp_bot.current_api_index + 1}"
    
    if unblocked:
        status_text += f"\n\n✅ {len(unblocked)} টি নাম্বার আনব্লক হয়েছে"
    
    bot.send_message(chat_id, status_text, parse_mode='Markdown')

def show_api_list(chat_id):
    """Show API keys list"""
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
    """Show blocked numbers list"""
    otp_bot.check_and_unblock_numbers()
    
    if not otp_bot.blocked_until:
        bot.send_message(chat_id, "✅ কোন ব্লক করা নাম্বার নেই")
        return
    
    text = "🚫 **ব্লক করা নাম্বার:**\n\n"
    for number, until in list(otp_bot.blocked_until.items())[:10]:
        remaining = (until - datetime.now()).seconds // 60
        text += f"📞 {number}\n"
        text += f"   └ আনব্লক হবে: {remaining} মিনিট পর\n"
    
    if len(otp_bot.blocked_until) > 10:
        text += f"\n... এবং আরও {len(otp_bot.blocked_until) - 10} টি"
    
    bot.send_message(chat_id, text, parse_mode='Markdown')

def show_settings(chat_id):
    """Show settings"""
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
    """Monitor and unblock expired numbers"""
    while True:
        try:
            unblocked = otp_bot.check_and_unblock_numbers()
            
            if unblocked:
                logger.info(f"Auto-unblocked {len(unblocked)} numbers")
            
            otp_bot.save_blocked_numbers()
            
        except Exception as e:
            logger.error(f"Error in monitor thread: {e}")
        
        time.sleep(60)

# মেইন এক্সিকিউশন
if __name__ == "__main__":
    print("""
    ╔══════════════════════════════════╗
    ║     TELEGRAM OTP BOT v4.0        ║
    ║        ২৪/৭ অটো রিজিউম           ║
    ╚══════════════════════════════════╝
    """)
    
    print("⚡ ফিচারসমূহ:")
    print("• কোন নাম্বার ব্লক হলে সাথে সাথে স্টপ")
    print("• ১ ঘন্টার টাইমার শুরু")
    print("• ১ ঘন্টা পর যেখানে বন্ধ ছিল সেখান থেকে শুরু")
    print("• ২৪/৭ চলতে থাকবে - স্টপ না দেওয়া পর্যন্ত")
    print("• API অটো সুইচ")
    print("• ব্লক লিস্ট মনিটর")
    
    otp_bot = OTPBot()
    
    monitor_thread = threading.Thread(target=monitor_blocked_numbers, daemon=True)
    monitor_thread.start()
    
    try:
        bot.send_message(ADMIN_ID, "🤖 **OTP বট v4.0 - ২৪/৭ মোড** চালু হয়েছে!\n\n/start কমান্ড দিন")
    except:
        print("⚠️ Admin notification failed")
    
    print("\n✅ Telegram bot is running in 24/7 mode...")
    print("📌 Commands: /start, /stop, /status, /add_api, /remove_api")
    bot.infinity_polling()    def save_config(self):
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
        
        if self.current_api_index < len(self.api_keys):
            self.api_keys[self.current_api_index]['error_count'] = self.api_keys[self.current_api_index].get('error_count', 0) + 1
            
            if self.api_keys[self.current_api_index]['error_count'] >= 3:
                self.api_keys[self.current_api_index]['is_active'] = False
                logger.warning(f"API key {self.current_api_index + 1} deactivated due to multiple errors")
                self.notify_admin(f"⚠️ API key {self.current_api_index + 1} ডিঅ্যাক্টিভ করা হয়েছে\nকারণ: ৩ বার ত্রুটি")
        
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
                del self.blocked_until[phone_number]
                self.save_blocked_numbers()
                logger.info(f"Number {phone_number} automatically unblocked after 1 hour")
        return False, 0
    
    def block_number(self, phone_number):
        """Block number for 1 hour"""
        block_until = datetime.now() + timedelta(hours=1)
        self.blocked_until[phone_number] = block_until
        self.stats['total_blocked'] += 1
        logger.info(f"Blocked {phone_number} until {block_until.strftime('%H:%M:%S')}")
        
        self.save_blocked_numbers()
        self.notify_admin(f"🚫 নাম্বার ব্লক: {phone_number}\n📅 আনব্লক হবে: {block_until.strftime('%H:%M:%S')}")
        
        return block_until
    
    def save_blocked_numbers(self):
        """Save blocked numbers to file"""
        blocked_data = {}
        for num, until in self.blocked_until.items():
            blocked_data[num] = until.isoformat()
        
        try:
            with open(self.blocked_numbers_file, 'w') as f:
                json.dump(blocked_data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving blocked numbers: {e}")
    
    def load_blocked_numbers(self):
        """Load blocked numbers from file"""
        if os.path.exists(self.blocked_numbers_file):
            try:
                with open(self.blocked_numbers_file, 'r') as f:
                    data = json.load(f)
                    current_time = datetime.now()
                    for num, until_str in data.items():
                        until = datetime.fromisoformat(until_str)
                        if current_time < until:
                            self.blocked_until[num] = until
                        else:
                            logger.info(f"Number {num} already unblocked (expired)")
                logger.info(f"Loaded {len(self.blocked_until)} blocked numbers")
            except Exception as e:
                logger.error(f"Error loading blocked numbers: {e}")
    
    def check_and_unblock_numbers(self):
        """Check and unblock expired numbers"""
        current_time = datetime.now()
        unblocked = []
        
        for number, block_until in list(self.blocked_until.items()):
            if current_time >= block_until:
                del self.blocked_until[number]
                unblocked.append(number)
                logger.info(f"Number {number} automatically unblocked")
        
        if unblocked:
            self.save_blocked_numbers()
            self.notify_admin(f"✅ {len(unblocked)} টি নাম্বার আনব্লক করা হয়েছে")
        
        return unblocked
    
    def update_status_message(self, success, failed, blocked, skipped, status_text=""):
        """Update the status message in Telegram"""
        if not self.status_message or not self.current_chat_id:
            return
        
        try:
            total = len(self.pending_numbers)
            current = self.current_processing_index
            progress = (current / total) * 100 if total > 0 else 0
            
            # পজ স্ট্যাটাস চেক
            pause_status = ""
            if self.paused_until:
                remaining = (self.paused_until - datetime.now()).seconds // 60
                pause_status = f"\n⏸️ **পজ করা আছে** - আর {remaining} মিনিট\nকারণ: {self.pause_reason}"
            
            message_text = (
                f"🚀 **OTP পাঠানো চলছে...**\n"
                f"📊 অগ্রগতি: {current}/{total} ({progress:.1f}%)\n"
                f"├ ✅ সফল: {success}\n"
                f"├ ❌ ব্যর্থ: {failed}\n"
                f"├ 🚫 ব্লক: {blocked}\n"
                f"├ ⏸️ স্কিপ: {skipped}\n"
                f"└ ⏱️ চালু আছে...\n"
                f"{pause_status}\n"
                f"{status_text}"
            )
            
            bot.edit_message_text(
                message_text,
                self.current_chat_id,
                self.status_message.message_id,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Error updating status: {e}")
    
    def send_otp(self, phone_number, use_whatsapp=False, max_retries=3):
        """Send OTP with automatic API switching and block handling"""
        
        # চেক করি নাম্বার ব্লক কিনা
        is_blocked, wait_time = self.is_number_blocked(phone_number)
        if is_blocked:
            return False, f"ব্লক", f"ব্লক করা আছে। আর {wait_time} মিনিট বাকি"
        
        # ফরম্যাট ফোন নাম্বার
        if not phone_number.startswith('+'):
            phone_number = '+' + phone_number.lstrip('0')
        
        for attempt in range(max_retries):
            app_id, api_key = self.get_current_api()
            
            if not app_id or not api_key:
                return False, "error", "❌ কোনো API key নেই!"
            
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
                
                if self.current_api_index < len(self.api_keys):
                    self.api_keys[self.current_api_index]['last_used'] = datetime.now().isoformat()
                
                # সফল
                if response.status_code == 200:
                    data = response.json()
                    
                    if data.get('status') == 'Blocked' and data.get('reason') == 'suspicious':
                        self.block_number(phone_number)
                        return False, "block", f"🚫 সন্দেহজনক! ১ ঘন্টার জন্য ব্লক"
                    
                    self.stats['total_sent'] += 1
                    return True, "success", "✅ OTP পাঠানো হয়েছে"
                
                # ক্রেডিট নেই
                elif response.status_code == 402 or "credits" in response.text.lower():
                    logger.warning("Insufficient credits! Switching API key...")
                    if self.switch_to_next_api():
                        continue
                    else:
                        return False, "error", "❌ কোনো API key তে ক্রেডিট নেই!"
                
                # ব্লক বা এক্সেস সমস্যা
                elif response.status_code == 403:
                    try:
                        error_data = response.json()
                        if error_data.get('status') == 'Blocked' and error_data.get('reason') == 'suspicious':
                            self.block_number(phone_number)
                            return False, "block", f"🚫 সন্দেহজনক! ১ ঘন্টার জন্য ব্লক"
                    except:
                        pass
                    
                    if self.switch_to_next_api():
                        continue
                    else:
                        return False, "error", "❌ এক্সেস নাই!"
                
                # অন্য ত্রুটি
                else:
                    logger.error(f"Error {response.status_code}")
                    try:
                        error_data = response.json()
                        if error_data.get('status') == 'Blocked' and error_data.get('reason') == 'suspicious':
                            self.block_number(phone_number)
                            return False, "block", f"🚫 সন্দেহজনক! ১ ঘন্টার জন্য ব্লক"
                    except:
                        pass
                    
                    if attempt < max_retries - 1:
                        if self.switch_to_next_api():
                            time.sleep(2)
                            continue
                    
                    self.stats['total_failed'] += 1
                    return False, "error", f"❌ ত্রুটি: {response.status_code}"
                    
            except Exception as e:
                logger.error(f"Error: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                self.stats['total_failed'] += 1
                return False, "error", f"❌ এরর: {str(e)[:50]}"
        
        return False, "error", "❌ সর্বোচ্চ চেষ্টা ব্যর্থ"
    
    def process_file(self, file_path, use_whatsapp, chat_id, message_id=None):
        """Process phone numbers from file - ব্লক হলে ১ ঘন্টা পজ, তারপর আবার শুরু"""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                self.pending_numbers = [line.strip() for line in f if line.strip()]
            
            if not self.pending_numbers:
                bot.send_message(chat_id, "❌ ফাইলটি খালি!")
                return
            
            # সেভ করা
            self.use_whatsapp = use_whatsapp
            self.current_chat_id = chat_id
            self.stop_requested = False
            self.paused_until = None
            self.pause_reason = None
            
            # স্ট্যাটাস মেসেজ
            self.status_message = bot.send_message(chat_id, 
                f"🚀 OTP পাঠানো শুরু...\n"
                f"📊 মোট নাম্বার: {len(self.pending_numbers)}\n"
                f"📱 চ্যানেল: {'WhatsApp' if use_whatsapp else 'SMS'}\n"
                f"⏸️ ব্লক হলে ১ ঘন্টা পজ হবে, তারপর আবার শুরু")
            
            success = 0
            failed = 0
            blocked = 0
            skipped = 0
            
            self.stats['start_time'] = datetime.now()
            self.current_processing_index = 0
            
            i = 0
            while i < len(self.pending_numbers) and not self.stop_requested:
                # চেক করি পজ করা আছে কিনা
                if self.paused_until:
                    if datetime.now() < self.paused_until:
                        # পজ অবস্থায় আছি
                        remaining = (self.paused_until - datetime.now()).seconds
                        wait_time = min(remaining, 60)  # সর্বোচ্চ ৬০ সেকেন্ড wait
                        
                        # স্ট্যাটাস আপডেট
                        self.update_status_message(
                            success, failed, blocked, skipped,
                            f"⏸️ পজ: {remaining//60} মিনিট বাকি..."
                        )
                        
                        time.sleep(wait_time)
                        continue
                    else:
                        # পজ শেষ, আবার শুরু
                        self.paused_until = None
                        self.pause_reason = None
                        self.notify_admin("▶️ ১ ঘন্টা পজ শেষ, আবার OTP পাঠানো শুরু হচ্ছে...")
                        
                        # স্ট্যাটাস আপডেট
                        self.update_status_message(
                            success, failed, blocked, skipped,
                            "▶️ পজ শেষ, আবার শুরু..."
                        )
                
                phone = self.pending_numbers[i]
                
                # চেক করি নাম্বার ব্লক কিনা
                is_blocked, wait_time = self.is_number_blocked(phone)
                if is_blocked:
                    skipped += 1
                    i += 1
                    continue
                
                # OTP পাঠাই
                result, result_type, message = self.send_otp(phone, use_whatsapp)
                
                if result:
                    success += 1
                else:
                    if result_type == "block":
                        blocked += 1
                        # ব্লক হলে পুরো প্রসেস ১ ঘন্টা পজ
                        self.paused_until = datetime.now() + timedelta(hours=1)
                        self.pause_reason = f"নাম্বার ব্লক: {phone}"
                        
                        # এডমিনকে জানাই
                        self.notify_admin(
                            f"🚫 **পুরো প্রসেস ১ ঘন্টা পজ**\n"
                            f"কারণ: {phone} ব্লক হয়েছে\n"
                            f"পজ শেষ: {(datetime.now() + timedelta(hours=1)).strftime('%H:%M:%S')}"
                        )
                        
                        # স্ট্যাটাস আপডেট
                        self.update_status_message(
                            success, failed, blocked, skipped,
                            f"🚫 ব্লক! ১ ঘন্টা পজ..."
                        )
                        
                        # পজ শেষ না হওয়া পর্যন্ত wait করব না, লুপের শুরুতে চেক করবে
                        continue
                    else:
                        failed += 1
                
                i += 1
                self.current_processing_index = i
                
                # প্রতি ৫টা নাম্বারে স্ট্যাটাস আপডেট
                if i % 5 == 0 or i == len(self.pending_numbers):
                    self.update_status_message(success, failed, blocked, skipped)
                
                # রেট লিমিট
                if i < len(self.pending_numbers) and not self.paused_until:
                    time.sleep(self.rate_limit_wait)
            
            # ফাইনাল রিপোর্ট
            time_taken = (datetime.now() - self.stats['start_time']).seconds
            report = (
                f"{'🛑 **স্টপ করা হয়েছে**' if self.stop_requested else '✅ **কাজ শেষ!**'}\n\n"
                f"📊 **রিপোর্ট:**\n"
                f"├ মোট নাম্বার: {len(self.pending_numbers)}\n"
                f"├ ✅ সফল: {success}\n"
                f"├ ❌ ব্যর্থ: {failed}\n"
                f"├ 🚫 ব্লক: {blocked}\n"
                f"├ ⏸️ স্কিপ: {skipped}\n"
                f"├ প্রসেসড: {i}\n"
                f"└ ⏱️ সময়: {time_taken} সেকেন্ড\n\n"
                f"📱 চ্যানেল: {'WhatsApp' if use_whatsapp else 'SMS'}"
            )
            
            bot.send_message(chat_id, report, parse_mode='Markdown')
            
            # বাকি নাম্বার থাকলে নোটিফাই
            remaining = len(self.pending_numbers) - i
            if remaining > 0 and self.stop_requested:
                bot.send_message(chat_id, f"⏸️ {remaining} টি নাম্বার বাকি আছে")
            
        except Exception as e:
            bot.send_message(chat_id, f"❌ এরর: {str(e)}")
        finally:
            self.is_running = False
            self.stop_requested = False
            self.paused_until = None
            self.pending_numbers = []
            self.status_message = None
            
            try:
                os.remove(file_path)
            except:
                pass

    def stop_processing(self):
        """Stop current processing gracefully"""
        if self.is_running:
            self.stop_requested = True
            self.notify_admin("🛑 প্রসেসিং বন্ধ করা হচ্ছে...")
            return True
        return False

    def get_status(self):
        """Get current processing status"""
        if not self.is_running:
            return "⏸️ কোন প্রসেস চলছে না"
        
        status = f"চলছে: {self.current_processing_index}/{len(self.pending_numbers)}"
        if self.paused_until:
            remaining = (self.paused_until - datetime.now()).seconds // 60
            status += f"\n⏸️ পজ: {remaining} মিনিট বাকি\nকারণ: {self.pause_reason}"
        
        return status

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
        "🤖 **OTP বট v4.0 - অটো রিজিউম**\n\n"
        "বৈশিষ্ট্য:\n"
        "• কোন নাম্বার ব্লক হলে ১ ঘন্টা পজ\n"
        "• ১ ঘন্টা পর আবার শুরু\n"
        "• স্টপ কমান্ড না দেওয়া পর্যন্ত চলবে\n\n"
        "নিচের বাটন ব্যবহার করুন:",
        reply_markup=markup,
        parse_mode='Markdown'
    )

@bot.message_handler(commands=['stop'])
def stop_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    if otp_bot.stop_processing():
        bot.reply_to(message, "🛑 প্রসেসিং বন্ধ করা হচ্ছে...")
    else:
        bot.reply_to(message, "❌ কোন প্রসেসিং চলছে না!")

@bot.message_handler(commands=['status'])
def status_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    show_status(message.chat.id)

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

@bot.message_handler(content_types=['document'])
def handle_document(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    if otp_bot.is_running:
        bot.reply_to(message, "❌ আগের টাস্ক চলছে! /stop দিয়ে বন্ধ করুন")
        return
    
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        file_path = f"telegram_{message.document.file_name}"
        with open(file_path, 'wb') as f:
            f.write(downloaded_file)
        
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
            "ফরম্যাট: প্রতিটি লাইনে এক একটি নাম্বার\n\n"
            "⚠️ নোট:\n"
            "• কোন নাম্বার ব্লক হলে ১ ঘন্টা পজ\n"
            "• ১ ঘন্টা পর আবার শুরু\n"
            "• স্টপ করতে /stop কমান্ড দিন"
        )
    
    elif call.data == "blocked_list":
        show_blocked_list(call.message.chat.id)
    
    elif call.data == "settings":
        show_settings(call.message.chat.id)
    
    elif call.data == "stop_task":
        if otp_bot.stop_processing():
            bot.answer_callback_query(call.id, "🛑 বন্ধ করা হচ্ছে...")
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
    """Show current status"""
    unblocked = otp_bot.check_and_unblock_numbers()
    
    status_text = (
        f"📊 **বর্তমান স্ট্যাটাস**\n\n"
        f"🔑 API Keys: {len(otp_bot.api_keys)}\n"
        f"✅ একটিভ Keys: {sum(1 for k in otp_bot.api_keys if k.get('is_active', True))}\n"
        f"🚫 ব্লক করা নাম্বার: {len(otp_bot.blocked_until)}\n"
        f"📊 টোটাল সেন্ট: {otp_bot.stats['total_sent']}\n"
        f"❌ টোটাল ফেইল: {otp_bot.stats['total_failed']}\n"
        f"🚫 টোটাল ব্লক: {otp_bot.stats['total_blocked']}\n"
        f"⚙️ চলছে: {'হ্যাঁ' if otp_bot.is_running else 'না'}\n"
    )
    
    if otp_bot.is_running:
        status_text += f"\n{otp_bot.get_status()}"
    
    if otp_bot.current_api_index < len(otp_bot.api_keys):
        status_text += f"\n\nবর্তমান API: {otp_bot.current_api_index + 1}"
    
    if unblocked:
        status_text += f"\n\n✅ {len(unblocked)} টি নাম্বার আনব্লক হয়েছে"
    
    bot.send_message(chat_id, status_text, parse_mode='Markdown')

def show_api_list(chat_id):
    """Show API keys list"""
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
    """Show blocked numbers list"""
    otp_bot.check_and_unblock_numbers()
    
    if not otp_bot.blocked_until:
        bot.send_message(chat_id, "✅ কোন ব্লক করা নাম্বার নেই")
        return
    
    text = "🚫 **ব্লক করা নাম্বার:**\n\n"
    for number, until in list(otp_bot.blocked_until.items())[:10]:
        remaining = (until - datetime.now()).seconds // 60
        text += f"📞 {number}\n"
        text += f"   └ আনব্লক হবে: {remaining} মিনিট পর\n"
    
    if len(otp_bot.blocked_until) > 10:
        text += f"\n... এবং আরও {len(otp_bot.blocked_until) - 10} টি"
    
    bot.send_message(chat_id, text, parse_mode='Markdown')

def show_settings(chat_id):
    """Show settings"""
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
    """Monitor and unblock expired numbers"""
    while True:
        try:
            unblocked = otp_bot.check_and_unblock_numbers()
            
            if unblocked:
                logger.info(f"Auto-unblocked {len(unblocked)} numbers")
            
            otp_bot.save_blocked_numbers()
            
        except Exception as e:
            logger.error(f"Error in monitor thread: {e}")
        
        time.sleep(60)

# মেইন এক্সিকিউশন
if __name__ == "__main__":
    print("""
    ╔══════════════════════════════════╗
    ║     TELEGRAM OTP BOT v4.0        ║
    ║   Auto Pause & Resume on Block   ║
    ╚══════════════════════════════════╝
    """)
    
    print("ফিচারসমূহ:")
    print("• কোন নাম্বার ব্লক হলে পুরো প্রসেস ১ ঘন্টা পজ")
    print("• ১ ঘন্টা পর আবার শুরু")
    print("• স্টপ না দেওয়া পর্যন্ত চলতে থাকবে")
    print("• API অটো সুইচ")
    print("• ব্লক লিস্ট মনিটর")
    
    otp_bot = OTPBot()
    
    monitor_thread = threading.Thread(target=monitor_blocked_numbers, daemon=True)
    monitor_thread.start()
    
    try:
        bot.send_message(ADMIN_ID, "🤖 বট v4.0 চালু হয়েছে!\n/start কমান্ড দিন")
    except:
        print("⚠️ Admin notification failed")
    
    print("✅ Telegram bot is running...")
    print("📌 Commands: /start, /stop, /status, /add_api, /remove_api")
    bot.infinity_polling()
