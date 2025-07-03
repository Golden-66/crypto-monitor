import requests
import time
import os
import pandas as pd
import math

# ==============================================================================
# 1. 用户配置区
# ==============================================================================
# -- Telegram 推送配置 --
TELEGRAM_BOT_TOKEN = '7993973623:AAE8AzKJ_DjWZ3N0KAQvGyT0EkMuTkVyKws' # <--- 在这里替换成您的Bot Token
TELEGRAM_CHAT_ID = '-4708011518' # <--- 在这里替换成您的Chat ID

# -- 运行逻辑配置 --
CHECK_INTERVAL_SECONDS = 60

CONFIG_FILE_PATH = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vRai92I6Uu_LIJZHK1eXom8AVjI-8MWfB9Q-TFdEKZkOOfTnowGWtEJDZ2bVKpLQN6E1e0dhRhpZuid/pub?gid=0&single=true&output=csv'
API_BATCH_SIZE = 30
# 固定链为Solana，不再从CSV读取
CHAIN = 'solana'

# 配置重载间隔（分钟），建议5-15分钟，避免过于频繁
RELOAD_CONFIG_INTERVAL_MINUTES = 10

# 定义警报点位的列名，顺序从高市值到低市值
ALERT_LEVEL_KEYS = ['low_mc0.618', 'low_mc0.786', 'low_mc0.85', 'low_mc0.94']

# ==============================================================================
# 2. 核心代码区 - 一般无需修改
# ==============================================================================

def format_large_number(n):
    if n >= 1_000_000_000:
        return f"{int(n / 1_000_000_000)}B"
    elif n >= 1_000_000:
        return f"{int(n / 1_000_000)}M"
    elif n >= 1_000:
        return f"{int(n / 1_000)}k"


def load_tokens_from_url(url):
    """从URL加载配置，并自动处理单位换算和空行"""
    try:
        # 关键改动：在URL后附加时间戳参数，强制刷新缓存
        cache_busting_url = f"{url}&_={int(time.time())}"

        df = pd.read_csv(cache_busting_url)

        # 增强鲁棒性：删除地址为空的行，防止空行被计入
        df.dropna(subset=['address'], inplace=True)
        if df.empty:
            return []

        for col in ALERT_LEVEL_KEYS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce') * 10000
            else:
                df[col] = pd.NA

        return df.to_dict('records')
    except Exception as e:
        print(f"❌ 加载配置文件时出错: {e}")
        return None


def update_monitored_list(current_list, new_list_data):
    """智能更新监控列表，保留已触发的警报状态"""
    # 将当前列表转换为以地址为键的字典，便于快速查找和更新
    current_map = {token['address']: token for token in current_list}

    updated_list = []

    # 遍历从Google Sheets新加载的数据
    for new_token_data in new_list_data:
        addr = new_token_data['address']

        if addr in current_map:
            # 如果代币已存在，则更新其目标，但保留警报状态
            existing_token = current_map[addr]
            for key, value in new_token_data.items():
                if key not in ['alert_triggered_status']:  # 不覆盖状态
                    existing_token[key] = value
            updated_list.append(existing_token)
        else:
            # 如果是新代币，则初始化状态并添加
            new_token_data['alert_triggered_status'] = {level: False for level in ALERT_LEVEL_KEYS}
            updated_list.append(new_token_data)
            print(f"🆕 检测到新代币并添加监控: {new_token_data.get('name', addr)}")

    # 检查是否有代币被删除
    new_addresses = {token['address'] for token in updated_list}
    for addr in list(current_map.keys()):
        if addr not in new_addresses:
            print(f"🗑️ 检测到代币被移除监控: {current_map[addr].get('name', addr)}")

    return updated_list


# (get_solana_token_data 和 send_telegram_alert 函数保持不变，这里省略以节省空间)
# (请确保您的文件中保留了这两个函数)
def get_solana_token_data(token_addresses):
    token_data = {}
    unique_addresses = sorted(list(set(token_addresses)))
    num_chunks = math.ceil(len(unique_addresses) / API_BATCH_SIZE)

    for i in range(num_chunks):
        address_chunk = unique_addresses[i * API_BATCH_SIZE: (i + 1) * API_BATCH_SIZE]
        address_string = ",".join(address_chunk)
        api_url = f"https://api.dexscreener.com/latest/dex/tokens/{address_string}"

        try:
            response = requests.get(api_url)
            if response.status_code == 200:
                data = response.json()
                if data and data.get('pairs'):
                    for pair in data['pairs']:
                        if pair.get('chainId') == CHAIN:
                            addr = pair['baseToken']['address']
                            price = float(pair.get('priceUsd', 0))
                            mc = int(float(pair.get('marketCap', 0)))
                            token_data[addr] = {'price': price, 'mc': mc}
            else:
                print(f"⚠️ API警告: {response.status_code}")
            time.sleep(0.2)
        except Exception as e:
            print(f"❌ 获取批量数据时发生网络错误: {e}")

    return token_data


def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN or 'YOUR_TELEGRAM_BOT_TOKEN' in TELEGRAM_BOT_TOKEN:
        print("Telegram Bot Token未配置，跳过发送。")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print(f"✅ Telegram 警报发送成功: {message.splitlines()[0]}")
        else:
            print(f"❌ 发送Telegram警报失败: {response.text}")
    except Exception as e:
        print(f"❌ 发送Telegram时发生网络错误: {e}")


def main():
    # 初始加载
    initial_list_data = load_tokens_from_url(CONFIG_FILE_PATH)
    if initial_list_data is None:
        print("首次加载配置失败，脚本退出。")
        return

    tokens_to_monitor = []
    # 初始化状态
    for token_data in initial_list_data:
        token_data['alert_triggered_status'] = {level: False for level in ALERT_LEVEL_KEYS}
        tokens_to_monitor.append(token_data)

    print(f"🚀 动态同步监控脚本已启动，初始加载 {len(tokens_to_monitor)} 个代币。")
    print(f"每 {RELOAD_CONFIG_INTERVAL_MINUTES} 分钟将从Google Sheets同步一次配置。")
    print("-" * 30)

    last_reload_time = time.time()

    while True:
        # --- 配置重载逻辑 ---
        current_time = time.time()
        if (current_time - last_reload_time) / 60 >= RELOAD_CONFIG_INTERVAL_MINUTES:
            print(f"\n🔄 定期重载配置（每{RELOAD_CONFIG_INTERVAL_MINUTES}分钟）...")
            new_data = load_tokens_from_url(CONFIG_FILE_PATH)
            if new_data is not None:
                tokens_to_monitor = update_monitored_list(tokens_to_monitor, new_data)
                print(f"配置同步完成，当前监控 {len(tokens_to_monitor)} 个代币。")
            else:
                print("⚠️ 本次重载配置失败，将继续使用旧配置。")
            last_reload_time = current_time

        # --- 核心检查逻辑 ---
        if not tokens_to_monitor:
            print("当前无代币需要监控，休眠中...")
            time.sleep(CHECK_INTERVAL_SECONDS)
            continue

        all_addresses = [token['address'] for token in tokens_to_monitor]
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n[{timestamp}] 开始新一轮数据检查...")

        latest_data = get_solana_token_data(all_addresses)

        # ... (这里的核心检查逻辑和上一版完全相同，这里省略以节省空间)
        # ... (请确保您的文件中保留了这部分检查逻辑)
        if latest_data:
            print(f"本轮成功获取 {len(latest_data)} 个代币的数据。")
            for token in tokens_to_monitor:
                addr = token['address']
                if addr in latest_data:
                    current_price = latest_data[addr]['price']
                    current_mc = latest_data[addr]['mc']

                    for level_key in ALERT_LEVEL_KEYS:
                        target_mc = token.get(level_key)
                        is_triggered = token['alert_triggered_status'][level_key]

                        if pd.notna(target_mc) and target_mc > 0 and not is_triggered:
                            if current_mc <= target_mc:
                                alert_level_name = level_key.replace("low_mc", "")
                                msg = (f"🪨 *市值下跌提醒 (点位: {alert_level_name})* 🪨\n\n"
                                       f"**代币:** {token['name']}\n"
                                       f"**地址:** {token['address']}\n"
                                       f"**当前市值:** `${format_large_number(current_mc)}`\n"
                                       f"**当前价格:** `${current_price:,.6f}`\n"
                                       f"**已跌破目标:** `${format_large_number(target_mc)}`")

                                if level_key == ALERT_LEVEL_KEYS[-1]:
                                    msg += "\n\n⚠️ *这是最后一个警报点位，可考虑从监控列表中移除此行。*"

                                send_telegram_alert(msg)
                                token['alert_triggered_status'][level_key] = True

        print(f"检查完毕，休眠 {CHECK_INTERVAL_SECONDS} 秒...")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()