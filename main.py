import os
import re
import json
import time
import random
import requests
import pytz
import pandas as pd
from datetime import datetime
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException

# --- ค่าคงที่ ---
SINGBURI_URL = "https://singburi.thaiwater.net/wl"
DISCHARGE_URL = 'https://tiwrm.hii.or.th/DATA/REPORT/php/chart/chaopraya/small/chaopraya.php'
LINE_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
LINE_GROUP_ID = os.environ.get('LINE_GROUP_ID') # Get Group ID from environment variable
LINE_PUSH_API_URL = "https://api.line.me/v2/bot/message/push"

# -- อ่านข้อมูลย้อนหลังจาก Excel --
THAI_MONTHS = {
    'มกราคม':1, 'กุมภาพันธ์':2, 'มีนาคม':3, 'เมษายน':4,
    'พฤษภาคม':5, 'มิถุนายน':6, 'กรกฎาคม':7, 'สิงหาคม':8,
    'กันยายน':9, 'ตุลาคม':10, 'พฤศจิกายน':11, 'ธันวาคม':12
}
def get_historical_from_excel(year_be: int) -> int | None:
    """
    อ่านไฟล์ระดับน้ำปี {year_be} จากทั้งโฟลเดอร์ data/ และโฟลเดอร์ปัจจุบัน
    คืนค่า discharge (ลบ.ม./วิ) ของวัน–เดือนปัจจุบัน (ตามเขตเวลาเอเชีย/กรุงเทพ)

    รองรับหลายรูปแบบคอลัมน์ เช่น:
      - มีคอลัมน์ 'เดือน' (ชื่อเดือนภาษาไทย) และ 'วันที่' (ตัวเลข) และคอลัมน์ปริมาณน้ำเป็น 'ปริมาณน้ำ (ลบ.ม./วินาที)' หรือ 'ปริมาณน้ำ (ลบ.ม./วิ)'
      - มีคอลัมน์ 'วันที่' เป็นชนิด datetime และคอลัมน์ค่าปริมาณน้ำอื่น ๆ (เช่น 'ค่า (ปี 2022)')
    """
    import pandas as pd
    # ค้นหาไฟล์ตามชื่อ
    possible_paths = [f"data/ระดับน้ำปี{year_be}.xlsx", f"ระดับน้ำปี{year_be}.xlsx", f"/mnt/data/ระดับน้ำปี{year_be}.xlsx"]
    file_path = None
    for p in possible_paths:
        if os.path.exists(p):
            file_path = p
            break
    if file_path is None:
        print(f"⚠️ ไม่พบไฟล์ข้อมูลย้อนหลังปี {year_be} ใน {possible_paths}")
        return None
    try:
        df = pd.read_excel(file_path)
        # หาคอลัมน์ค่าปริมาณน้ำที่อาจจะมีหลายชื่อ
        discharge_col = None
        for col in df.columns:
            name = str(col)
            if 'ลบ.ม.' in name or 'discharge' in name or 'ค่า' in name:
                discharge_col = col
                break
        if discharge_col is None:
            print(f"⚠️ ไฟล์ {file_path} ไม่มีคอลัมน์ปริมาณน้ำที่รู้จัก")
            return None
        df = df.rename(columns={discharge_col: 'discharge'})
        # ตรวจว่าเรามีคอลัมน์ 'เดือน' และ 'วันที่' แยกหรือไม่
        if 'เดือน' in df.columns and 'วันที่' in df.columns:
            # กรณีนี้ 'วันที่' เป็นตัวเลข (ไม่ใช่ datetime) และ 'เดือน' เป็นชื่อภาษาไทย
            df['month_num'] = df['เดือน'].map(THAI_MONTHS)
            df['day_num'] = df['วันที่']
        elif 'วันที่' in df.columns:
            # แปลง 'วันที่' ให้เป็น datetime หากไม่ใช่
            if not pd.api.types.is_datetime64_any_dtype(df['วันที่']):
                df['date'] = pd.to_datetime(df['วันที่'], errors='coerce')
            else:
                df['date'] = df['วันที่']
            df['month_num'] = df['date'].dt.month
            df['day_num'] = df['date'].dt.day
        else:
            print(f"⚠️ ไฟล์ {file_path} ไม่มีคอลัมน์ 'วันที่' ที่คาดหวัง")
            return None
        # วันที่วันนี้
        now = datetime.now(pytz.timezone('Asia/Bangkok'))
        today_d = now.day
        today_m = now.month
        match = df[(df['day_num'] == today_d) & (df['month_num'] == today_m)]
        if not match.empty:
            val = match.iloc[0]['discharge']
            # แปลงเป็นตัวเลข int หากจำเป็น
            try:
                # หากมี comma
                val_int = int(val)
            except Exception:
                try:
                    val_int = int(str(val).replace(',', ''))
                except Exception:
                    val_int = None
            if val_int is not None:
                print(f"✅ พบข้อมูลย้อนหลังสำหรับปี {year_be}: {val_int} ลบ.ม./วินาที (ไฟล์: {file_path})")
                return val_int
        print(f"⚠️ ไม่พบข้อมูลสำหรับวันที่ {today_d}/{today_m} ในไฟล์ปี {year_be} (ไฟล์: {file_path})")
        return None
    except Exception as e:
        print(f"❌ ERROR: ไม่สามารถโหลดข้อมูลย้อนหลังจาก Excel ได้ ({file_path}): {e}")
        return None

# --- ดึงระดับน้ำอินทร์บุรี ---
def get_inburi_data(url: str, timeout: int = 45, retries: int = 3):
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    
    driver = None
    for attempt in range(retries):
        try:
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
            driver.get(url)
            WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "th[scope='row']"))
            )
            html = driver.page_source
            
            soup = BeautifulSoup(html, "html.parser")
            for th in soup.select("th[scope='row']"):
                if "อินทร์บุรี" in th.get_text(strip=True):
                    tr = th.find_parent("tr")
                    cols = tr.find_all("td")
                    water_level = float(cols[1].get_text(strip=True))
                    bank_level = 13.0
                    print(f"✅ พบข้อมูลอินทร์บุรี: ระดับน้ำ={water_level}, ระดับตลิ่ง={bank_level} (ค่าโดยประมาณ)")
                    if driver: driver.quit()
                    return water_level, bank_level
            
            print("⚠️ ไม่พบข้อมูลสถานี 'อินทร์บุรี' ในตาราง")
            if driver: driver.quit()
            return None, None
        except StaleElementReferenceException:
            print(f"⚠️ เจอ Stale Element Reference (ครั้งที่ {attempt + 1}/{retries}), กำลังลองใหม่...")
            if driver: driver.quit()
            time.sleep(3)
            continue
        except Exception as e:
            print(f"❌ ERROR: get_inburi_data: {e}")
            if driver: driver.quit()
            return None, None
    return None, None

# --- ดึงข้อมูลเขื่อนเจ้าพระยา (เพิ่ม Cache Busting) ---
def fetch_chao_phraya_dam_discharge(url: str, timeout: int = 30):
    try:
        # เพิ่ม headers เพื่อพยายามไม่ให้ติด cache
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        }
        # เพิ่มตัวเลขสุ่มต่อท้าย URL (Cache Busting)
        cache_buster_url = f"{url}?cb={random.randint(10000, 99999)}"
        
        response = requests.get(cache_buster_url, headers=headers, timeout=10)
        response.raise_for_status()
        response.encoding = 'utf-8'
        
        match = re.search(r'var json_data = (\[.*\]);', response.text)
        if not match:
            print("❌ ERROR: ไม่พบข้อมูล JSON ในหน้าเว็บ")
            return None
            
        json_string = match.group(1)
        data = json.loads(json_string)
        
        water_storage = data[0]['itc_water']['C13']['storage']
        if water_storage is not None:
            if isinstance(water_storage, (int, float)):
                value = float(water_storage)
            else:
                value = float(str(water_storage).replace(',', ''))
                
            print(f"✅ พบข้อมูลเขื่อนเจ้าพระยา: {value}")
            return value
    except Exception as e:
        print(f"❌ ERROR: fetch_chao_phraya_dam_discharge: {e}")
    return None

# --- วิเคราะห์และสร้างข้อความ ---
def analyze_and_create_message(inburi_level, dam_discharge, bank_height, hist_2567=None, hist_2565=None, hist_2554=None):
    distance_to_bank = bank_height - inburi_level
    
    ICON = ""
    HEADER = ""
    summary_text = ""

    if dam_discharge > 2400 or distance_to_bank < 1.0:
        ICON = "🟥"
        HEADER = "‼️ ประกาศเตือนภัยระดับสูงสุด ‼️"
        summary_text = "คำแนะนำ:\n1. เตรียมพร้อมอพยพหากอยู่ในพื้นที่เสี่ยง\n2. ขนย้ายทรัพย์สินขึ้นที่สูงโดยด่วน\n3. งดใช้เส้นทางสัญจรริมแม่น้ำ"
    elif dam_discharge > 1800 or distance_to_bank < 2.0:
        ICON = "🟨"
        HEADER = "‼️ ประกาศเฝ้าระวัง ‼️"
        summary_text = "คำแนะนำ:\n1. บ้านเรือนริมตลิ่งนอกคันกั้นน้ำ ให้เริ่มขนของขึ้นที่สูง\n2. ติดตามสถานการณ์อย่างใกล้ชิด"
    else:
        ICON = "🟩"
        HEADER = "สถานะปกติ"
        summary_text = "สถานการณ์น้ำยังปกติ ใช้ชีวิตได้ตามปกติครับ"

    now = datetime.now(pytz.timezone('Asia/Bangkok'))
    TIMESTAMP = now.strftime('%d/%m/%Y %H:%M')

    msg_lines = [
        f"{ICON} {HEADER}",
        "",
        f"📍 รายงานสถานการณ์น้ำเจ้าพระยา จ.อ.อินทร์บุรี",
        f"🗓️ วันที่: {TIMESTAMP} น.",
        "",
        "🌊 ระดับน้ำ + ระดับตลิ่ง",
        f"  • อินทร์บุรี: {inburi_level:.2f} ม.รทก.",
        f"  • ตลิ่ง: {bank_height:.2f} ม.รทก. (ต่ำกว่า {distance_to_bank:.2f} ม.)",
        "",
        "💧 ปริมาณน้ำปล่อยเขื่อนเจ้าพระยา",
        f"  {dam_discharge:,} ลบ.ม./วินาที",
        "",
        "🔄 เปรียบเทียบย้อนหลัง",
    ]
    if hist_2567 is not None:
        msg_lines.append(f"  • ปี 2567: {hist_2567:,} ลบ.ม./วินาที")
    if hist_2565 is not None:
        msg_lines.append(f"  • ปี 2565: {hist_2565:,} ลบ.ม./วินาที")
    if hist_2554 is not None:
        msg_lines.append(f"  • ปี 2554: {hist_2554:,} ลบ.ม./วินาที")
    msg_lines += [
        "",
        summary_text
    ]
    return "\n".join(msg_lines)

# --- สร้างข้อความ Error ---
def create_error_message(inburi_status, discharge_status):
    now = datetime.now(pytz.timezone('Asia/Bangkok'))
    return (
        f"⚙️❌ เกิดข้อผิดพลาดในการดึงข้อมูล ❌⚙️\n"
        f"เวลา: {now.strftime('%d/%m/%Y %H:%M')} น.\n\n"
        f"• สถานะข้อมูลระดับน้ำอินทร์บุรี: {inburi_status}\n"
        f"• สถานะข้อมูลเขื่อนเจ้าพระยา: {discharge_status}\n\n"
        f"กรุณาตรวจสอบ Log บน GitHub Actions เพื่อดูรายละเอียดข้อผิดพลาดครับ"
    )

# --- ส่งข้อความ LINE (ฉบับปรับปรุง) ---
def send_line_push(message):
    if not LINE_TOKEN:
        print("❌ ไม่พบ LINE_CHANNEL_ACCESS_TOKEN!")
        return
    if not LINE_GROUP_ID:
        print("❌ ไม่พบ LINE_GROUP_ID! กรุณาตั้งค่าใน GitHub Secrets")
        return

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_TOKEN}"}
    # Payload for Push Message
    payload = {
        "to": LINE_GROUP_ID,
        "messages": [{"type": "text", "text": message}]
    }
    
    retries = 3 # จำนวนครั้งที่จะลองใหม่
    delay = 5   # เริ่มต้นรอ 5 วินาที

    for i in range(retries):
        try:
            # Use the PUSH API URL
            res = requests.post(LINE_PUSH_API_URL, headers=headers, json=payload, timeout=15)
            res.raise_for_status() 
            
            print("✅ ส่งข้อความ Push สำเร็จ!")
            return
            
        except requests.exceptions.HTTPError as err:
            if err.response.status_code == 429:
                print(f"⚠️ API แจ้งว่าส่งถี่เกินไป (429), กำลังลองใหม่ในอีก {delay} วินาที... (ครั้งที่ {i + 1}/{retries})")
                time.sleep(delay)
                delay *= 2
            else:
                print(f"❌ ERROR: LINE Push (HTTP Error): {err}")
                print(f"    Response: {err.response.text}") # Print error response for more details
                break
        except Exception as e:
            print(f"❌ ERROR: LINE Push (General Error): {e}")
            break

    print("❌ ไม่สามารถส่งข้อความได้หลังจากการพยายามหลายครั้ง")


# --- Main ---
if __name__ == "__main__":
    print("=== เริ่มการทำงานระบบแจ้งเตือนน้ำอินทร์บุรี ===")
    
    inburi_cache_buster_url = f"{SINGBURI_URL}?cb={random.randint(10000, 99999)}"
    
    inburi_level, bank_level = get_inburi_data(inburi_cache_buster_url)
    dam_discharge = fetch_chao_phraya_dam_discharge(DISCHARGE_URL)
    
    # ดึงข้อมูลย้อนหลังจาก Excel (ตามวันวันนี้)
    hist_2567 = get_historical_from_excel(2567)
    hist_2565 = get_historical_from_excel(2565)
    hist_2554 = get_historical_from_excel(2554)

    if inburi_level is not None and bank_level is not None and dam_discharge is not None:
        final_message = analyze_and_create_message(
            inburi_level,
            dam_discharge,
            bank_level,
            hist_2567=hist_2567,
            hist_2565=hist_2565,
            hist_2554=hist_2554,
        )
    else:
        inburi_status = "สำเร็จ" if inburi_level is not None else "ล้มเหลว"
        discharge_status = "สำเร็จ" if dam_discharge is not None else "ล้มเหลว"
        final_message = create_error_message(inburi_status, discharge_status)

    print("\n📤 ข้อความที่จะแจ้งเตือน:")
    print(final_message)
    print("\n🚀 ส่งข้อความไปยัง LINE...")
    send_line_push(final_message)
    print("✅ เสร็จสิ้นการทำงาน")
