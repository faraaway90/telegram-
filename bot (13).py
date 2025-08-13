import json
import datetime
import logging
import asyncio
import time
import os
import random
import sys
import locale

# === UTF-8 ENCODING FIXES FOR VPS ===
# Set environment variables for UTF-8 encoding
os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['LC_ALL'] = 'C.UTF-8'
os.environ['LANG'] = 'C.UTF-8'

# Set locale to UTF-8
try:
    locale.setlocale(locale.LC_ALL, 'C.UTF-8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
    except locale.Error:
        pass  # Fallback to system default

# Fix encoding for VPS deployment - Enhanced version
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception as e:
    print(f"Warning: Could not reconfigure stdout/stderr encoding: {e}")

from flask import Flask, render_template, jsonify
from threading import Thread

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters,
)

# Configure logging with UTF-8 support
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout)
    ])
logger = logging.getLogger(__name__)
logger.info("🚀 Bot starting with UTF-8 encoding support...")

# === CONFIG LOADING ===
try:
    with open("config.json", encoding='utf-8') as f:
        config = json.load(f)
except FileNotFoundError:
    logger.error("config.json not found!")
    exit(1)

# Get bot token from environment variable or config
BOT_TOKEN = os.getenv("BOT_TOKEN", config.get("bot_token", ""))
if not BOT_TOKEN:
    logger.error("Bot token not found in environment variables or config!")
    exit(1)

print(f"Using bot token: {BOT_TOKEN[:10]}...")  # Debug print
ADMIN_USERNAME = config["admin"]
ADMIN_ID = config["admin_id"]
MIN_WITHDRAW = config["min_withdraw"]
TASKS = config["tasks"]
DAILY_LIMIT = config["daily_limit"]
BONUS_REFERRAL = config["referral_bonus"]
CURRENCY = config["currency"]
PAYOUT_CONFIG = config.get("payout_config", {})

# Data storage
users = {}
withdrawals = []
user_tasks = {}
payout_requests = {}  # New: Store payout requests
completed_tasks = {}  # Track completed tasks per user per day

# === EMOJI CONSTANTS FOR VPS COMPATIBILITY ===
# Using Unicode escape sequences to prevent corruption during copy-paste
EMOJIS = {
    'rocket': '\U0001F680',
    'money': '\U0001F4B0',
    'chart': '\U0001F4CA',
    'check': '\U00002705',
    'people': '\U0001F465',
    'chart_up': '\U0001F4C8',
    'card': '\U0001F4B3',
    'payout': '\U0001F4B8',
    'info': '\U00002139\U0000FE0F',
    'thumbs_up': '\U0001F44D',
    'comment': '\U0001F4AC',
    'bell': '\U0001F514',
    'eyes': '\U0001F440',
    'clock': '\U000023F0',
    'news': '\U0001F4F0',
    'back': '\U0001F519',
    'target': '\U0001F3AF',
    'diamond': '\U0001F48E',
    'fire': '\U0001F525',
    'star': '\U00002B50',
    'warning': '\U000026A0\U0000FE0F',
    'error': '\U0000274C',
    'party': '\U0001F389',
    'folder': '\U0001F4CB',
    'link': '\U0001F517',
    'time': '\U0000231B',
    'loading': '\U000023F3',
    'done': '\U00002728',
    'down_arrow': '\U0001F447',
    'video': '\U0001F4FA',
    'heart': '\U00002764\U0000FE0F',
    'gift': '\U0001F381',
    'handshake': '\U0001F91D'
}

def safe_emoji(emoji_key, fallback=""):
    """Safely get emoji with fallback"""
    return EMOJIS.get(emoji_key, fallback)

def format_message(message):
    """Format message with proper UTF-8 encoding"""
    try:
        # Ensure message is properly encoded
        if isinstance(message, str):
            return message.encode('utf-8').decode('utf-8')
        return str(message)
    except (UnicodeEncodeError, UnicodeDecodeError):
        # Fallback: remove problematic characters
        return message.encode('ascii', errors='ignore').decode('ascii')

# === FLASK SETUP ===
app = Flask(__name__)

@app.route('/')
def home():
    pending_payouts = len([req for req in payout_requests.values() if req['status'] == 'pending'])
    return render_template('dashboard.html', 
                         users=len(users), 
                         withdrawals=len(withdrawals),
                         active_tasks=len(user_tasks),
                         pending_payouts=pending_payouts)

@app.route('/health')
def health():
    return jsonify({
        "status": "ok",
        "users": len(users),
        "active_tasks": len(user_tasks),
        "withdrawals": len(withdrawals),
        "payout_requests": len(payout_requests)
    })

@app.route('/api/stats')
def stats():
    total_balance = sum(user['balance'] for user in users.values())
    total_earned = sum(user['total_earned'] for user in users.values())
    pending_payouts = len([req for req in payout_requests.values() if req['status'] == 'pending'])
    approved_payouts = len([req for req in payout_requests.values() if req['status'] == 'approved'])
    
    return jsonify({
        "total_users": len(users),
        "total_balance": round(total_balance, 2),
        "total_earned": round(total_earned, 2),
        "active_tasks": len(user_tasks),
        "pending_withdrawals": len(withdrawals),
        "pending_payouts": pending_payouts,
        "approved_payouts": approved_payouts
    })

def run_flask():
    app.run(host='0.0.0.0', port=5000, debug=False)

# === UTILITY FUNCTIONS ===
def load_data():
    global users, withdrawals, payout_requests
    try:
        with open("data.json", "r", encoding='utf-8') as f:
            data = json.load(f)
            users = data.get("users", {})
            withdrawals = data.get("withdrawals", [])
            payout_requests = data.get("payout_requests", {})
            logger.info(f"Loaded data: {len(users)} users, {len(withdrawals)} withdrawals, {len(payout_requests)} payout requests")
    except FileNotFoundError:
        logger.info("No data.json found, starting fresh")
        users = {}
        withdrawals = []
        payout_requests = {}

def save_data():
    data = {
        "users": users,
        "withdrawals": withdrawals,
        "payout_requests": payout_requests
    }
    with open("data.json", "w", encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def get_user(user_id):
    if str(user_id) not in users:
        users[str(user_id)] = {
            "balance": 0.0,
            "total_earned": 0.0,
            "tasks_completed": 0,
            "referrals": 0,
            "daily_earned": 0.0,
            "last_activity": datetime.datetime.now().isoformat(),
            "joined": datetime.datetime.now().isoformat()
        }
        save_data()
    return users[str(user_id)]

def can_earn_today(user_id):
    user = get_user(user_id)
    today = datetime.datetime.now().date()
    last_activity = datetime.datetime.fromisoformat(user["last_activity"]).date()
    
    logger.info(f"can_earn_today check: user {user_id}, today: {today}, last_activity: {last_activity}, daily_earned: {user['daily_earned']}, limit: {DAILY_LIMIT}")
    
    if last_activity < today:
        logger.info(f"Resetting daily earnings for user {user_id} - new day")
        user["daily_earned"] = 0.0
        # Reset completed tasks for new day
        today_str = today.isoformat()
        if str(user_id) not in completed_tasks:
            completed_tasks[str(user_id)] = {}
        # Clean old dates
        completed_tasks[str(user_id)] = {date: tasks for date, tasks in completed_tasks[str(user_id)].items() if date == today_str}
        
    can_earn = user["daily_earned"] < DAILY_LIMIT
    logger.info(f"can_earn_today result for user {user_id}: {can_earn}")
    return can_earn

def has_completed_task_today(user_id, task_key):
    """Check if user has already completed this specific task today"""
    today = datetime.datetime.now().date().isoformat()
    if str(user_id) not in completed_tasks:
        completed_tasks[str(user_id)] = {}
    if today not in completed_tasks[str(user_id)]:
        completed_tasks[str(user_id)][today] = []
    return task_key in completed_tasks[str(user_id)][today]

def mark_task_completed(user_id, task_key):
    """Mark specific task as completed for today"""
    today = datetime.datetime.now().date().isoformat()
    if str(user_id) not in completed_tasks:
        completed_tasks[str(user_id)] = {}
    if today not in completed_tasks[str(user_id)]:
        completed_tasks[str(user_id)][today] = []
    if task_key not in completed_tasks[str(user_id)][today]:
        completed_tasks[str(user_id)][today].append(task_key)
    save_data()

def get_available_tasks_in_category(user_id, category):
    """Get list of available tasks in a category that user hasn't completed today"""
    if category not in TASKS or "links" not in TASKS[category]:
        return []
    
    available_tasks = []
    for i, link in enumerate(TASKS[category]["links"]):
        task_key = f"{category}_{i+1}"  # like_1, like_2, comment_1, etc.
        if not has_completed_task_today(user_id, task_key):
            available_tasks.append({
                "task_key": task_key,
                "link": link,
                "number": i + 1
            })
    return available_tasks

def add_earnings(user_id, amount):
    user = get_user(user_id)
    user["balance"] += amount
    user["total_earned"] += amount
    user["daily_earned"] += amount
    user["tasks_completed"] += 1
    user["last_activity"] = datetime.datetime.now().isoformat()
    save_data()

def start_task_timer(user_id, task_key):
    """Start a timer for task completion"""
    user_tasks[f"{user_id}_{task_key}"] = time.time()

def is_task_completed(user_id, task_key):
    """Check if task wait time has passed"""
    task_start = user_tasks.get(f"{user_id}_{task_key}")
    if not task_start:
        return False
    
    # Extract category from task_key (e.g., "like_1" -> "like")
    category = task_key.split("_")[0] if "_" in task_key else task_key
    if category not in TASKS:
        return False
        
    required_wait = TASKS[category]["wait"]
    elapsed = time.time() - task_start
    return elapsed >= required_wait

def get_remaining_time(user_id, task_key):
    """Get remaining wait time for task"""
    task_start = user_tasks.get(f"{user_id}_{task_key}")
    if not task_start:
        return 0
    
    # Extract category from task_key (e.g., "like_1" -> "like")
    category = task_key.split("_")[0] if "_" in task_key else task_key
    if category not in TASKS:
        return 0
        
    required_wait = TASKS[category]["wait"]
    elapsed = time.time() - task_start
    remaining = max(0, required_wait - elapsed)
    return int(remaining)

def format_time(seconds):
    """Format time in human readable format"""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds//60}m {seconds%60}s"
    else:
        return f"{seconds//3600}h {(seconds%3600)//60}m"

def get_task_buttons(task_key, user_id=None):
    """Get inline keyboard buttons for task links"""
    task = TASKS[task_key]
    buttons = []
    
    # Check if user has already completed this task today
    if user_id and has_completed_task_today(user_id, task_key):
        # Show completed status instead of links
        buttons.append([InlineKeyboardButton(f"{safe_emoji('check')} Task Already Completed Today", callback_data="task_completed")])
        return buttons
    
    if "links" in task and task["links"]:
        print(f"DEBUG: Task {task_key} has {len(task['links'])} links available")
        # Create rows of 2 buttons each
        for i in range(0, len(task["links"]), 2):
            row = []
            for j in range(2):
                if i + j < len(task["links"]):
                    link_num = i + j + 1
                    if task_key == "visit":
                        button_text = f"{safe_emoji('news')} Article {link_num}"
                    elif task_key == "subscribe":
                        button_text = f"{safe_emoji('bell')} Channel {link_num}"
                    else:
                        button_text = f"{safe_emoji('video')} Video {link_num}"
                    row.append(InlineKeyboardButton(button_text, url=task["links"][i + j]))
            buttons.append(row)
    elif "link" in task:
        if task_key == "visit":
            buttons.append([InlineKeyboardButton(f"{safe_emoji('news')} Article Link", url=task["link"])])
        else:
            buttons.append([InlineKeyboardButton(f"{safe_emoji('link')} Task Link", url=task["link"])])
    
    return buttons

def generate_request_id():
    """Generate unique request ID"""
    return f"REQ_{int(time.time())}_{random.randint(1000, 9999)}"

def create_payout_request(user_id, username, amount, payment_method, payment_address):
    """Create a new payout request"""
    request_id = generate_request_id()
    payout_requests[request_id] = {
        "user_id": user_id,
        "username": username,
        "amount": amount,
        "payment_method": payment_method,
        "payment_address": payment_address,
        "status": "pending",
        "created_at": datetime.datetime.now().isoformat(),
        "processed_at": None,
        "admin_note": ""
    }
    save_data()
    return request_id

def get_user_pending_requests(user_id):
    """Get pending requests for a user"""
    return [req for req in payout_requests.values() if req['user_id'] == user_id and req['status'] == 'pending']

# === BOT HANDLERS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start command handler"""
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    # Handle referral
    if context.args and len(context.args) > 0:
        referrer_id = context.args[0]
        if referrer_id != str(user_id) and referrer_id in users:
            users[referrer_id]["referrals"] += 1
            add_earnings(int(referrer_id), BONUS_REFERRAL)
            message = format_message(f"{safe_emoji('party', '🎉')} You got a new referral! Bonus: {BONUS_REFERRAL}{CURRENCY}")
            await context.bot.send_message(
                chat_id=int(referrer_id),
                text=message)
    
    welcome_message = format_message(f"""
{safe_emoji('rocket', '🚀')} **Welcome to BitcoRise Earning Bot!**

{safe_emoji('money', '💰')} **Earn cryptocurrency by completing simple tasks:**
• Like YouTube videos: {TASKS['like']['reward']}{CURRENCY}
• Comment on videos: {TASKS['comment']['reward']}{CURRENCY}
• Subscribe to channels: {TASKS['subscribe']['reward']}{CURRENCY}
• Watch videos (45s): {TASKS['watch']['reward']}{CURRENCY}
• Watch videos (3min): {TASKS['watch_3min']['reward']}{CURRENCY}
• Visit articles: {TASKS['visit']['reward']}{CURRENCY}

{safe_emoji('diamond', '💎')} **Your Stats:**
{safe_emoji('money', '💰')} Balance: {user['balance']}{CURRENCY}
{safe_emoji('chart', '📊')} Total Earned: {user['total_earned']}{CURRENCY}
{safe_emoji('check', '✅')} Tasks Completed: {user['tasks_completed']}
{safe_emoji('people', '👥')} Referrals: {user['referrals']}

{safe_emoji('chart_up', '📈')} **Daily Limit:** {DAILY_LIMIT}{CURRENCY}
{safe_emoji('payout', '💸')} **Min Payout:** {MIN_WITHDRAW}{CURRENCY}

Ready to start earning? Choose an option below! {safe_emoji('down_arrow')}
""")
    
    keyboard = [
        [InlineKeyboardButton(f"{safe_emoji('money', '💰')} Start Tasks", callback_data="tasks"),
         InlineKeyboardButton(f"{safe_emoji('chart', '📊')} My Balance", callback_data="balance")],
        [InlineKeyboardButton(f"{safe_emoji('payout', '💸')} Withdraw", callback_data="withdraw"),
         InlineKeyboardButton(f"{safe_emoji('people', '👥')} Invite Friends", callback_data="invite")],
        [InlineKeyboardButton(f"{safe_emoji('info', 'ℹ️')} Info", callback_data="info"),
         InlineKeyboardButton(f"{safe_emoji('folder', '📋')} My Tasks", callback_data="my_tasks")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_message, reply_markup=reply_markup, parse_mode='Markdown')

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Balance command handler"""
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    # Calculate daily remaining earnings
    daily_remaining = max(0, DAILY_LIMIT - user['daily_earned'])
    
    balance_message = format_message(f"""
{safe_emoji('chart', '📊')} **Your Earnings Dashboard**

{safe_emoji('money', '💰')} **Current Balance:** {user['balance']:.2f}{CURRENCY}
{safe_emoji('chart_up', '📈')} **Total Earned:** {user['total_earned']:.2f}{CURRENCY}
{safe_emoji('check', '✅')} **Tasks Completed:** {user['tasks_completed']}
{safe_emoji('people', '👥')} **Referrals:** {user['referrals']}

{safe_emoji('time', '⏱️')} **Today's Progress:**
• Earned today: {user['daily_earned']:.2f}{CURRENCY}
• Remaining today: {daily_remaining:.2f}{CURRENCY}

{safe_emoji('payout', '💸')} **Withdrawal Info:**
• Minimum: {MIN_WITHDRAW}{CURRENCY}
• Status: {'✅ Available' if user['balance'] >= MIN_WITHDRAW else '❌ Below minimum'}

{safe_emoji('gift', '🎁')} **Referral Bonus:** {BONUS_REFERRAL}{CURRENCY} per referral
""")
    
    keyboard = [
        [InlineKeyboardButton(f"{safe_emoji('money', '💰')} Start Tasks", callback_data="tasks")],
        [InlineKeyboardButton(f"{safe_emoji('payout', '💸')} Withdraw", callback_data="withdraw")],
        [InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back to Menu", callback_data="start_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(balance_message, reply_markup=reply_markup, parse_mode='Markdown')

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user = get_user(user_id)
    
    if query.data == "tasks":
        await show_tasks(query, user_id, user)
    elif query.data == "balance":
        await show_balance(query, user_id, user)
    elif query.data == "withdraw":
        await show_withdraw_menu(query, user_id, user)
    elif query.data == "invite":
        await show_invite_menu(query, user_id, user)
    elif query.data == "info":
        await show_info_menu(query, user_id, user)
    elif query.data == "my_tasks":
        await show_my_tasks(query, user_id, user)
    elif query.data == "start_menu":
        await show_start_menu(query, user_id, user)
    elif query.data.startswith("task_"):
        task_key = query.data.split("_")[1]
        await handle_task(query, user_id, task_key)
    elif query.data.startswith("complete_"):
        task_key = query.data.split("_", 1)[1]  # Handle task keys with underscores like "like_1"
        await complete_task(query, user_id, task_key)
    elif query.data.startswith("category_"):
        category = query.data.split("_")[1]
        await show_category_tasks(query, user_id, category)
    elif query.data.startswith("individual_"):
        task_key = query.data.split("_", 1)[1]  # Handle task keys with underscores like "like_1"
        await handle_individual_task(query, user_id, task_key)
    elif query.data == "back_tasks":
        await show_tasks(query, user_id, user)
    elif query.data.startswith("payout_"):
        method = query.data.split("_")[1]
        await handle_payout_method(query, user_id, method, context)
    elif query.data == "task_completed":
        await query.edit_message_text(f"{safe_emoji('done', '✅')} This task has already been completed today! Try other available tasks to earn more.")
    else:
        await query.edit_message_text("Unknown command. Use /start to begin.")

async def show_tasks(query, user_id, user):
    """Show available tasks menu"""
    logger.info(f"show_tasks called for user {user_id}, daily_earned: {user['daily_earned']}, daily_limit: {DAILY_LIMIT}")
    
    if not can_earn_today(user_id):
        logger.info(f"User {user_id} cannot earn today - daily limit reached")
        daily_remaining = max(0, DAILY_LIMIT - user['daily_earned'])
        message = format_message(f"""
{safe_emoji('warning', '⚠️')} **Daily Limit Reached**

You've reached your daily earning limit of {DAILY_LIMIT}{CURRENCY}.
Come back tomorrow to earn more!

{safe_emoji('chart', '📊')} **Today's Earnings:** {user['daily_earned']:.2f}{CURRENCY}
""")
        keyboard = [[InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back to Menu", callback_data="start_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        return
    
    message = format_message(f"""
{safe_emoji('target', '🎯')} **Available Tasks**

Choose a task to start earning {CURRENCY}:

{safe_emoji('thumbs_up', '👍')} **Like Video** - {TASKS['like']['reward']}{CURRENCY}
{safe_emoji('comment', '💬')} **Comment on Video** - {TASKS['comment']['reward']}{CURRENCY}
{safe_emoji('bell', '🔔')} **Subscribe to Channel** - {TASKS['subscribe']['reward']}{CURRENCY}
{safe_emoji('eyes', '👀')} **Watch Video (45s)** - {TASKS['watch']['reward']}{CURRENCY}
{safe_emoji('video', '📺')} **Watch Video (3min)** - {TASKS['watch_3min']['reward']}{CURRENCY}
{safe_emoji('news', '📰')} **Visit Article** - {TASKS['visit']['reward']}{CURRENCY}

{safe_emoji('chart_up', '📈')} **Daily Earnings:** {user['daily_earned']:.2f}/{DAILY_LIMIT}{CURRENCY}
""")
    
    # Create keyboard with available task counts for each category
    row1 = []
    row2 = []
    row3 = []
    
    # Row 1: Like and Comment
    like_available = len(get_available_tasks_in_category(user_id, "like"))
    if like_available > 0:
        row1.append(InlineKeyboardButton(f"{safe_emoji('thumbs_up', '👍')} Like Videos ({like_available})", callback_data="category_like"))
    else:
        row1.append(InlineKeyboardButton(f"{safe_emoji('check', '✅')} Like Videos (0)", callback_data="task_completed"))
        
    comment_available = len(get_available_tasks_in_category(user_id, "comment"))
    if comment_available > 0:
        row1.append(InlineKeyboardButton(f"{safe_emoji('comment', '💬')} Comments ({comment_available})", callback_data="category_comment"))
    else:
        row1.append(InlineKeyboardButton(f"{safe_emoji('check', '✅')} Comments (0)", callback_data="task_completed"))
    
    # Row 2: Subscribe and Watch 45s
    subscribe_available = len(get_available_tasks_in_category(user_id, "subscribe"))
    if subscribe_available > 0:
        row2.append(InlineKeyboardButton(f"{safe_emoji('bell', '🔔')} Subscribe ({subscribe_available})", callback_data="category_subscribe"))
    else:
        row2.append(InlineKeyboardButton(f"{safe_emoji('check', '✅')} Subscribe (0)", callback_data="task_completed"))
        
    watch_available = len(get_available_tasks_in_category(user_id, "watch"))
    if watch_available > 0:
        row2.append(InlineKeyboardButton(f"{safe_emoji('eyes', '👀')} Watch 45s ({watch_available})", callback_data="category_watch"))
    else:
        row2.append(InlineKeyboardButton(f"{safe_emoji('check', '✅')} Watch 45s (0)", callback_data="task_completed"))
    
    # Row 3: Watch 3min and Visit Article
    watch_3min_available = len(get_available_tasks_in_category(user_id, "watch_3min"))
    if watch_3min_available > 0:
        row3.append(InlineKeyboardButton(f"{safe_emoji('video', '📺')} Watch 3min ({watch_3min_available})", callback_data="category_watch_3min"))
    else:
        row3.append(InlineKeyboardButton(f"{safe_emoji('check', '✅')} Watch 3min (0)", callback_data="task_completed"))
        
    visit_available = len(get_available_tasks_in_category(user_id, "visit"))
    if visit_available > 0:
        row3.append(InlineKeyboardButton(f"{safe_emoji('news', '📰')} Visit Article ({visit_available})", callback_data="category_visit"))
    else:
        row3.append(InlineKeyboardButton(f"{safe_emoji('check', '✅')} Visit Article (0)", callback_data="task_completed"))
    
    keyboard = [
        row1,
        row2, 
        row3,
        [InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back to Menu", callback_data="start_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')

async def show_category_tasks(query, user_id, category):
    """Show individual tasks within a category"""
    if category not in TASKS:
        await query.edit_message_text("Invalid category.")
        return
    
    available_tasks = get_available_tasks_in_category(user_id, category)
    
    if not available_tasks:
        message = format_message(f"""
{safe_emoji('done', '✅')} **All Tasks Completed**

You have completed all available tasks in the {TASKS[category]['name']} category today!

Come back tomorrow for more tasks.
""")
        keyboard = [[InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back to Tasks", callback_data="tasks")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        return
    
    task_info = TASKS[category]
    message = format_message(f"""
{safe_emoji(task_info['emoji'], '📺')} **{task_info['name']} Tasks**

{safe_emoji('info', 'ℹ️')} **Description:** {task_info['description']}
{safe_emoji('money', '💰')} **Reward:** {task_info['reward']}{CURRENCY} each
{safe_emoji('clock', '⏰')} **Wait Time:** {task_info['wait']} seconds

{safe_emoji('list', '📋')} **Available Tasks:** {len(available_tasks)}

Choose a task to start:
""")
    
    # Create buttons for available tasks
    keyboard = []
    for task in available_tasks:
        task_button = InlineKeyboardButton(
            f"{safe_emoji('play', '▶️')} Task {task['number']}", 
            callback_data=f"individual_{task['task_key']}"
        )
        keyboard.append([task_button])
    
    # Add back button
    keyboard.append([InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back to Tasks", callback_data="tasks")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')

async def handle_individual_task(query, user_id, task_key):
    """Handle individual task selection and start timer"""
    # Check if user already completed this specific task today
    if has_completed_task_today(user_id, task_key):
        message = format_message(f"""
{safe_emoji('done', '✅')} **Task Already Completed**

You have already completed this specific task today!

Try other available tasks to earn more.
""")
        keyboard = [[InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back to Tasks", callback_data="tasks")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        return
    
    if not can_earn_today(user_id):
        message = format_message(f"""
{safe_emoji('warning', '⚠️')} **Daily Limit Reached**

You've reached your daily earning limit of {DAILY_LIMIT}{CURRENCY}.
Come back tomorrow to earn more!
""")
        keyboard = [[InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back to Menu", callback_data="start_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        return
    
    # Extract category and task number
    category = task_key.split("_")[0]
    task_number = task_key.split("_")[1]
    
    if category not in TASKS:
        await query.edit_message_text("Invalid task category.")
        return
    
    # Get the specific link for this task
    if "links" not in TASKS[category] or not TASKS[category]["links"]:
        await query.edit_message_text("No links available for this task category.")
        return
    
    task_index = int(task_number) - 1
    if task_index >= len(TASKS[category]["links"]):
        await query.edit_message_text("Invalid task number.")
        return
    
    task_link = TASKS[category]["links"][task_index]
    task_info = TASKS[category]
    
    # Start the timer
    start_task_timer(user_id, task_key)
    
    message = format_message(f"""
{safe_emoji(task_info['emoji'], '📺')} **{task_info['name']} - Task {task_number}**

{safe_emoji('info', 'ℹ️')} **Instructions:**
{task_info['description']}

{safe_emoji('money', '💰')} **Reward:** {task_info['reward']}{CURRENCY}
{safe_emoji('clock', '⏰')} **Wait Time:** {task_info['wait']} seconds

{safe_emoji('link', '🔗')} **Task Link:**
Click the link below to complete the task:
""")
    
    # Create keyboard with task link and claim button
    keyboard = [
        [InlineKeyboardButton(f"{safe_emoji('play', '▶️')} Start Task {task_number}", url=task_link)],
        [InlineKeyboardButton(f"{safe_emoji('loading', '⏳')} Claim Reward ({task_info['wait']}s)", callback_data=f"complete_{task_key}")],
        [InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back to {task_info['name']} Tasks", callback_data=f"category_{category}")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')

async def show_balance(query, user_id, user):
    """Show balance information"""
    daily_remaining = max(0, DAILY_LIMIT - user['daily_earned'])
    
    balance_message = format_message(f"""
{safe_emoji('chart', '📊')} **Your Earnings Dashboard**

{safe_emoji('money', '💰')} **Current Balance:** {user['balance']:.2f}{CURRENCY}
{safe_emoji('chart_up', '📈')} **Total Earned:** {user['total_earned']:.2f}{CURRENCY}
{safe_emoji('check', '✅')} **Tasks Completed:** {user['tasks_completed']}
{safe_emoji('people', '👥')} **Referrals:** {user['referrals']}

{safe_emoji('time', '⏱️')} **Today's Progress:**
• Earned today: {user['daily_earned']:.2f}{CURRENCY}
• Remaining today: {daily_remaining:.2f}{CURRENCY}

{safe_emoji('payout', '💸')} **Withdrawal Info:**
• Minimum: {MIN_WITHDRAW}{CURRENCY}
• Status: {'✅ Available' if user['balance'] >= MIN_WITHDRAW else '❌ Below minimum'}

{safe_emoji('gift', '🎁')} **Referral Bonus:** {BONUS_REFERRAL}{CURRENCY} per referral
""")
    
    keyboard = [
        [InlineKeyboardButton(f"{safe_emoji('money', '💰')} Start Tasks", callback_data="tasks")],
        [InlineKeyboardButton(f"{safe_emoji('payout', '💸')} Withdraw", callback_data="withdraw")],
        [InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back to Menu", callback_data="start_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(balance_message, reply_markup=reply_markup, parse_mode='Markdown')

async def show_withdraw_menu(query, user_id, user):
    """Show withdrawal menu"""
    if user['balance'] < MIN_WITHDRAW:
        message = format_message(f"""
{safe_emoji('warning', '⚠️')} **Insufficient Balance**

Your current balance: {user['balance']:.2f}{CURRENCY}
Minimum withdrawal: {MIN_WITHDRAW}{CURRENCY}

You need {MIN_WITHDRAW - user['balance']:.2f}{CURRENCY} more to withdraw.

{safe_emoji('money', '💰')} Complete more tasks to reach the minimum!
""")
        keyboard = [
            [InlineKeyboardButton(f"{safe_emoji('money', '💰')} Start Tasks", callback_data="tasks")],
            [InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back to Menu", callback_data="start_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        return
    
    # Check for pending requests
    pending_requests = get_user_pending_requests(user_id)
    if pending_requests:
        message = format_message(f"""
{safe_emoji('loading', '⏳')} **Withdrawal Request Pending**

You have {len(pending_requests)} pending withdrawal request(s).
Please wait for admin approval before submitting a new request.

{safe_emoji('time', '⏱️')} **Your pending requests:**
""")
        for req in pending_requests:
            created_date = datetime.datetime.fromisoformat(req['created_at']).strftime("%Y-%m-%d %H:%M")
            message += f"• {req['amount']}{CURRENCY} via {req['payment_method']} ({created_date})\n"
        
        keyboard = [[InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back to Menu", callback_data="start_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        return
    
    message = format_message(f"""
{safe_emoji('payout', '💸')} **Withdrawal Menu**

{safe_emoji('money', '💰')} **Available Balance:** {user['balance']:.2f}{CURRENCY}
{safe_emoji('payout', '💸')} **Minimum Withdrawal:** {MIN_WITHDRAW}{CURRENCY}

{safe_emoji('card', '💳')} **Choose your payout method:**

Available methods:
""")
    
    keyboard = []
    for method_key, method_info in PAYOUT_CONFIG.items():
        if method_info.get('enabled', True):
            keyboard.append([InlineKeyboardButton(
                f"{method_info.get('emoji', '💳')} {method_info['name']}", 
                callback_data=f"payout_{method_key}"
            )])
    
    keyboard.append([InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back to Menu", callback_data="start_menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')

async def show_invite_menu(query, user_id, user):
    """Show invite friends menu with referral link and stats"""
    try:
        bot_username = query.bot.username if hasattr(query.bot, 'username') else "bitcorise_bot"
    except:
        bot_username = "bitcorise_bot"
    referral_link = f"https://t.me/{bot_username}?start={user_id}"
    
    message = format_message(f"""
{safe_emoji('people', '👥')} **Invite Friends & Earn More!**

{safe_emoji('gift', '🎁')} **Referral Bonus:** {BONUS_REFERRAL}{CURRENCY} per friend
{safe_emoji('handshake', '🤝')} **Your Referrals:** {user['referrals']} friends
{safe_emoji('money', '💰')} **Earned from Referrals:** {user['referrals'] * BONUS_REFERRAL:.2f}{CURRENCY}

{safe_emoji('link', '🔗')} **Your Referral Link:**
`{referral_link}`

{safe_emoji('rocket', '🚀')} **How it works:**
1. Share your referral link with friends
2. When they join and start earning, you get {BONUS_REFERRAL}{CURRENCY}
3. No limit on referrals - invite more, earn more!

{safe_emoji('star', '⭐')} **Tips for more referrals:**
• Share in crypto groups
• Post on social media
• Tell your friends about easy earning!
""")
    
    keyboard = [
        [InlineKeyboardButton(f"{safe_emoji('link', '🔗')} Share Link", 
                             url=f"https://t.me/share/url?url={referral_link}&text=Join this amazing earning bot!")],
        [InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back to Menu", callback_data="start_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')

async def show_info_menu(query, user_id, user):
    """Show information menu with bot details and FAQ"""
    message = format_message(f"""
{safe_emoji('info', 'ℹ️')} **Bot Information**

{safe_emoji('rocket', '🚀')} **Welcome to BitcoRise Earning Bot!**
Your gateway to earning cryptocurrency through simple tasks.

{safe_emoji('money', '💰')} **How to Earn:**
• Complete YouTube tasks (like, comment, subscribe, watch)
• Visit articles and websites
• Invite friends for referral bonuses
• Daily earning limit: {DAILY_LIMIT}{CURRENCY}

{safe_emoji('payout', '💸')} **Withdrawals:**
• Minimum withdrawal: {MIN_WITHDRAW}{CURRENCY}
• Multiple payout methods available
• Requests processed within 24-48 hours

{safe_emoji('target', '🎯')} **Task Rewards:**
• Like videos: {TASKS['like']['reward']}{CURRENCY}
• Comments: {TASKS['comment']['reward']}{CURRENCY}
• Subscriptions: {TASKS['subscribe']['reward']}{CURRENCY}
• Watch 45s: {TASKS['watch']['reward']}{CURRENCY}
• Watch 3min: {TASKS['watch_3min']['reward']}{CURRENCY}
• Visit articles: {TASKS['visit']['reward']}{CURRENCY}

{safe_emoji('gift', '🎁')} **Referral Program:**
• Earn {BONUS_REFERRAL}{CURRENCY} per successful referral
• No limit on referrals
• Bonus credited instantly when friend joins

{safe_emoji('warning', '⚠️')} **Important Rules:**
• Complete tasks honestly
• Wait for task timers before claiming
• One account per person
• Follow all task requirements

{safe_emoji('heart', '❤️')} **Support:**
Contact @{ADMIN_USERNAME} for assistance
""")
    
    keyboard = [
        [InlineKeyboardButton(f"{safe_emoji('money', '💰')} Start Tasks", callback_data="tasks"),
         InlineKeyboardButton(f"{safe_emoji('people', '👥')} Invite Friends", callback_data="invite")],
        [InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back to Menu", callback_data="start_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')

async def show_my_tasks(query, user_id, user):
    """Show user's active/completed tasks"""
    active_tasks = []
    for key, start_time in user_tasks.items():
        if key.startswith(f"{user_id}_"):
            task_key = key.split("_")[1]
            remaining = get_remaining_time(user_id, task_key)
            if remaining > 0:
                active_tasks.append((task_key, remaining))
    
    message = format_message(f"""
{safe_emoji('folder', '📋')} **My Tasks**

{safe_emoji('chart', '📊')} **Your Stats:**
• Total tasks completed: {user['tasks_completed']}
• Total earned: {user['total_earned']:.2f}{CURRENCY}
• Today's earnings: {user['daily_earned']:.2f}{CURRENCY}

{safe_emoji('clock', '⏰')} **Active Tasks:**
""")
    
    if active_tasks:
        for task_key, remaining in active_tasks:
            task_name = TASKS[task_key]['name']
            time_left = format_time(remaining)
            message += f"• {task_name} - {time_left} remaining\n"
        message += f"\n{safe_emoji('info', 'ℹ️')} Wait for timers to complete, then claim your rewards!"
    else:
        message += f"No active tasks. Start new tasks to earn more!\n\n{safe_emoji('money', '💰')} Ready to earn? Choose tasks below!"
    
    keyboard = [
        [InlineKeyboardButton(f"{safe_emoji('money', '💰')} Start New Tasks", callback_data="tasks")],
        [InlineKeyboardButton(f"{safe_emoji('chart', '📊')} My Balance", callback_data="balance")],
        [InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back to Menu", callback_data="start_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')

async def show_start_menu(query, user_id, user):
    """Show main start menu"""
    welcome_message = format_message(f"""
{safe_emoji('rocket', '🚀')} **Welcome to BitcoRise Earning Bot!**

{safe_emoji('money', '💰')} **Earn cryptocurrency by completing simple tasks:**
• Like YouTube videos: {TASKS['like']['reward']}{CURRENCY}
• Comment on videos: {TASKS['comment']['reward']}{CURRENCY}
• Subscribe to channels: {TASKS['subscribe']['reward']}{CURRENCY}
• Watch videos (45s): {TASKS['watch']['reward']}{CURRENCY}
• Watch videos (3min): {TASKS['watch_3min']['reward']}{CURRENCY}
• Visit articles: {TASKS['visit']['reward']}{CURRENCY}

{safe_emoji('diamond', '💎')} **Your Stats:**
{safe_emoji('money', '💰')} Balance: {user['balance']:.2f}{CURRENCY}
{safe_emoji('chart', '📊')} Total Earned: {user['total_earned']:.2f}{CURRENCY}
{safe_emoji('check', '✅')} Tasks Completed: {user['tasks_completed']}
{safe_emoji('people', '👥')} Referrals: {user['referrals']}

{safe_emoji('chart_up', '📈')} **Daily Limit:** {DAILY_LIMIT}{CURRENCY}
{safe_emoji('payout', '💸')} **Min Payout:** {MIN_WITHDRAW}{CURRENCY}

Ready to start earning? Choose an option below! {safe_emoji('down_arrow')}
""")
    
    keyboard = [
        [InlineKeyboardButton(f"{safe_emoji('money', '💰')} Start Tasks", callback_data="tasks"),
         InlineKeyboardButton(f"{safe_emoji('chart', '📊')} My Balance", callback_data="balance")],
        [InlineKeyboardButton(f"{safe_emoji('payout', '💸')} Withdraw", callback_data="withdraw"),
         InlineKeyboardButton(f"{safe_emoji('people', '👥')} Invite Friends", callback_data="invite")],
        [InlineKeyboardButton(f"{safe_emoji('info', 'ℹ️')} Info", callback_data="info"),
         InlineKeyboardButton(f"{safe_emoji('folder', '📋')} My Tasks", callback_data="my_tasks")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(welcome_message, reply_markup=reply_markup, parse_mode='Markdown')

async def handle_task(query, user_id, task_key):
    """Handle task selection"""
    if not can_earn_today(user_id):
        await query.edit_message_text(f"{safe_emoji('warning', '⚠️')} You've reached your daily earning limit. Come back tomorrow!")
        return
    
    if task_key not in TASKS:
        await query.edit_message_text(f"{safe_emoji('error', '❌')} Invalid task selected.")
        return
    
    # Check if user already completed this task today
    if has_completed_task_today(user_id, task_key):
        message = format_message(f"""
{safe_emoji('done', '✅')} **Task Already Completed**

You have already completed this task today!
Each task can only be completed once per day.

{safe_emoji('money', '💰')} Try other available tasks to earn more.
""")
        keyboard = [
            [InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back to Tasks", callback_data="back_tasks")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        return
    
    task = TASKS[task_key]
    
    # Check if user already has this task active
    if f"{user_id}_{task_key}" in user_tasks:
        remaining = get_remaining_time(user_id, task_key)
        if remaining > 0:
            time_left = format_time(remaining)
            message = format_message(f"""
{safe_emoji('clock', '⏰')} **Task In Progress**

You already started this task!
Please wait {time_left} before claiming your reward.

{safe_emoji('info', 'ℹ️')} Complete the task requirements and wait for the timer.
""")
            keyboard = [
                [InlineKeyboardButton(f"{safe_emoji('check', '✅')} Check Progress", callback_data=f"complete_{task_key}")],
                [InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back to Tasks", callback_data="back_tasks")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')
            return
    
    # Start the task
    start_task_timer(user_id, task_key)
    
    message = format_message(f"""
{safe_emoji('target', '🎯')} **{task['name']}**

{safe_emoji('money', '💰')} **Reward:** {task['reward']}{CURRENCY}
{safe_emoji('clock', '⏰')} **Wait Time:** {task['wait']} seconds

{safe_emoji('info', 'ℹ️')} **Instructions:**
{task['description']}

{safe_emoji('warning', '⚠️')} **Important:**
Complete the task, then wait {task['wait']} seconds before claiming your reward.

Click the link(s) below to start:
""")
    
    # Get task buttons
    task_buttons = get_task_buttons(task_key, user_id)
    
    # Add claim and back buttons
    claim_button = [InlineKeyboardButton(f"{safe_emoji('loading', '⏳')} Claim Reward ({task['wait']}s)", callback_data=f"complete_{task_key}")]
    back_button = [InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back to Tasks", callback_data="back_tasks")]
    
    keyboard = task_buttons + [claim_button, back_button]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')

async def complete_task(query, user_id, task_key):
    """Handle task completion"""
    # Extract category from individual task key (e.g., "like_1" -> "like")
    category = task_key.split("_")[0] if "_" in task_key else task_key
    if category not in TASKS:
        await query.edit_message_text(f"{safe_emoji('error', '❌')} Invalid task.")
        return
    
    if not can_earn_today(user_id):
        await query.edit_message_text(f"{safe_emoji('warning', '⚠️')} You've reached your daily earning limit. Come back tomorrow!")
        return
    
    # Check if user already completed this task today
    if has_completed_task_today(user_id, task_key):
        message = format_message(f"""
{safe_emoji('done', '✅')} **Task Already Completed**

You have already completed this task today!
Each task can only be completed once per day.

{safe_emoji('money', '💰')} Try other available tasks to earn more.
""")
        keyboard = [
            [InlineKeyboardButton(f"{safe_emoji('money', '💰')} More Tasks", callback_data="tasks")],
            [InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Main Menu", callback_data="start_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        return
        
    task = TASKS[category]
    
    # Check if task is completed
    if is_task_completed(user_id, task_key):
        # Mark task as completed to prevent re-completion
        mark_task_completed(user_id, task_key)
        
        # Award the reward
        add_earnings(user_id, task['reward'])
        
        # Remove from active tasks
        if f"{user_id}_{task_key}" in user_tasks:
            del user_tasks[f"{user_id}_{task_key}"]
        
        user = get_user(user_id)
        message = format_message(f"""
{safe_emoji('done', '✨')} **Task Completed!**

{safe_emoji('money', '💰')} **Reward Earned:** {task['reward']}{CURRENCY}
{safe_emoji('chart', '📊')} **New Balance:** {user['balance']:.2f}{CURRENCY}

{safe_emoji('party', '🎉')} Great job! Ready for another task?

{safe_emoji('chart_up', '📈')} **Daily Progress:** {user['daily_earned']:.2f}/{DAILY_LIMIT}{CURRENCY}
""")
        
        keyboard = [
            [InlineKeyboardButton(f"{safe_emoji('money', '💰')} More Tasks", callback_data="tasks")],
            [InlineKeyboardButton(f"{safe_emoji('chart', '📊')} My Balance", callback_data="balance")],
            [InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Main Menu", callback_data="start_menu")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        # Still waiting
        remaining = get_remaining_time(user_id, task_key)
        time_left = format_time(remaining)
        
        message = format_message(f"""
{safe_emoji('clock', '⏰')} **Please Wait...**

{safe_emoji('loading', '⏳')} Time remaining: **{time_left}**

Complete the task requirements and wait for the timer to finish.
You can check back anytime!

{safe_emoji('info', 'ℹ️')} **Task:** {task['name']}
{safe_emoji('money', '💰')} **Reward:** {task['reward']}{CURRENCY}
""")
        
        keyboard = [
            [InlineKeyboardButton(f"{safe_emoji('check', '✅')} Check Again", callback_data=f"complete_{task_key}")],
            [InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back to Tasks", callback_data="back_tasks")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')

async def handle_payout_method(query, user_id, method, context):
    """Handle payout method selection"""
    user = get_user(user_id)
    
    if method not in PAYOUT_CONFIG:
        await query.edit_message_text(f"{safe_emoji('error', '❌')} Invalid payout method.")
        return
    
    method_info = PAYOUT_CONFIG[method]
    username = query.from_user.username or f"User_{user_id}"
    
    message = format_message(f"""
{safe_emoji('payout', '💸')} **Withdrawal Request**

{method_info.get('emoji', '💳')} **Method:** {method_info['name']}
{safe_emoji('money', '💰')} **Amount:** {user['balance']:.2f}{CURRENCY}

{safe_emoji('info', 'ℹ️')} **Instructions:**
{method_info.get('instructions', 'Please provide your payment address.')}

Please send your {method_info['name']} address in the next message.

{safe_emoji('warning', '⚠️')} **Important:**
- Double-check your address before sending
- Requests are processed within 24-48 hours
- Contact @{ADMIN_USERNAME} for support
""")
    
    keyboard = [[InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Back", callback_data="withdraw")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')
    
    # Store the selected method for the next message
    context.user_data['payout_method'] = method
    context.user_data['awaiting_address'] = True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages (mainly for payout addresses)"""
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if context.user_data.get('awaiting_address'):
        method = context.user_data.get('payout_method')
        if method and method in PAYOUT_CONFIG:
            payment_address = update.message.text.strip()
            username = update.effective_user.username or f"User_{user_id}"
            
            # Create payout request
            request_id = create_payout_request(
                user_id=user_id,
                username=username,
                amount=user['balance'],
                payment_method=PAYOUT_CONFIG[method]['name'],
                payment_address=payment_address
            )
            
            # Deduct balance
            user['balance'] = 0.0
            save_data()
            
            # Clear user data
            context.user_data.clear()
            
            # Notify admin
            admin_message = format_message(f"""
{safe_emoji('payout', '💸')} **New Withdrawal Request**

**Request ID:** {request_id}
**User:** @{username} (ID: {user_id})
**Amount:** {payout_requests[request_id]['amount']:.2f}{CURRENCY}
**Method:** {payout_requests[request_id]['payment_method']}
**Address:** {payment_address}
**Date:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
""")
            
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=admin_message,
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Failed to notify admin: {e}")
            
            # Confirm to user
            confirmation_message = format_message(f"""
{safe_emoji('done', '✨')} **Withdrawal Request Submitted!**

{safe_emoji('folder', '📋')} **Request ID:** {request_id}
{safe_emoji('money', '💰')} **Amount:** {payout_requests[request_id]['amount']:.2f}{CURRENCY}
{safe_emoji('card', '💳')} **Method:** {payout_requests[request_id]['payment_method']}

{safe_emoji('clock', '⏰')} **Processing Time:** 24-48 hours
{safe_emoji('info', 'ℹ️')} You'll be notified when processed

{safe_emoji('money', '💰')} Keep earning while you wait!
""")
            
            keyboard = [
                [InlineKeyboardButton(f"{safe_emoji('money', '💰')} Start Tasks", callback_data="tasks")],
                [InlineKeyboardButton(f"{safe_emoji('back', '🔙')} Main Menu", callback_data="start_menu")]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(confirmation_message, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text("Invalid payment method. Please try again with /start")
    else:
        # Default response for other messages
        await update.message.reply_text("Use /start to begin or choose an option from the menu.")

# === ADMIN COMMANDS ===
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to view bot statistics"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("Access denied.")
        return
    
    total_users = len(users)
    total_balance = sum(user['balance'] for user in users.values())
    total_earned = sum(user['total_earned'] for user in users.values())
    total_tasks = sum(user['tasks_completed'] for user in users.values())
    total_referrals = sum(user['referrals'] for user in users.values())
    pending_payouts = len([req for req in payout_requests.values() if req['status'] == 'pending'])
    
    stats_message = format_message(f"""
{safe_emoji('chart', '📊')} **Bot Statistics**

{safe_emoji('people', '👥')} **Users:** {total_users}
{safe_emoji('money', '💰')} **Total Balance:** {total_balance:.2f}{CURRENCY}
{safe_emoji('chart_up', '📈')} **Total Earned:** {total_earned:.2f}{CURRENCY}
{safe_emoji('check', '✅')} **Total Tasks:** {total_tasks}
{safe_emoji('handshake', '🤝')} **Total Referrals:** {total_referrals}
{safe_emoji('payout', '💸')} **Pending Payouts:** {pending_payouts}
{safe_emoji('clock', '⏰')} **Active Tasks:** {len(user_tasks)}

{safe_emoji('info', 'ℹ️')} **Daily Limit:** {DAILY_LIMIT}{CURRENCY}
{safe_emoji('payout', '💸')} **Min Withdrawal:** {MIN_WITHDRAW}{CURRENCY}
""")
    
    await update.message.reply_text(stats_message, parse_mode='Markdown')

async def admin_payouts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to view pending payouts"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("Access denied.")
        return
    
    pending_requests = [req for req in payout_requests.values() if req['status'] == 'pending']
    
    if not pending_requests:
        await update.message.reply_text(f"{safe_emoji('info', 'ℹ️')} No pending payout requests.")
        return
    
    message = format_message(f"{safe_emoji('payout', '💸')} **Pending Payout Requests**\n\n")
    
    for req_id, req in payout_requests.items():
        if req['status'] == 'pending':
            created_date = datetime.datetime.fromisoformat(req['created_at']).strftime("%Y-%m-%d %H:%M")
            message += f"**ID:** {req_id}\n"
            message += f"**User:** @{req['username']} (ID: {req['user_id']})\n"
            message += f"**Amount:** {req['amount']:.2f}{CURRENCY}\n"
            message += f"**Method:** {req['payment_method']}\n"
            message += f"**Address:** `{req['payment_address']}`\n"
            message += f"**Date:** {created_date}\n"
            message += "---\n\n"
    
    await update.message.reply_text(message, parse_mode='Markdown')

# === BOT SETUP ===
def main() -> None:
    """Start the bot"""
    # Load data
    load_data()
    
    # Start Flask in background
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Create bot application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("admin_stats", admin_stats))
    application.add_handler(CommandHandler("admin_payouts", admin_payouts))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Run the bot
    logger.info("Bot is starting...")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
