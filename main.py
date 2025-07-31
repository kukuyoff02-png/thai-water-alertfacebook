import os
import re
import json
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

# --- ค่าคงที่ ---
SINGBURI_URL = "https://singburi.thaiwater.net/wl"
DISCHARGE_URL = 'https://tiwrm.hii.or.th/DATA/REPORT/php/chart/chaopraya/small/chaopraya.php'
HISTORICAL_DATA_FILE = 'dam_discharge_history.xlsx - Sheet1.csv' # <-- ชื่อไฟล์ข้อมูลย้อนหลัง
LINE_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
LINE_API_URL = "https://api.line.me/v2/bot/message/broadcast"

# --- ดึงระดับน้ำอินทร์บุรี ---
def get_inburi_data(url: str, timeout: int = 30):
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    
    driver = None
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
                return water_level, bank_level
        print("⚠️ ไม่พบข้อมูลสถานี 'อินทร์บุรี' ในตาราง")
        return None, None
    except Exception as e:
        print(f"❌ ERROR: get_inburi_data: {e}")
        return None, None
    finally:
        if driver:
            driver.quit()

# --- ดึงข้อมูลเขื่อนเจ้าพระยา ---
def fetch_chao_phraya_dam_discharge(url: str, timeout: int = 30):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        response.encoding = 'utf-8'
        
        match = re.search(r'var json_data = (\[.*\]);', response.text)
        if not match:
            print("❌ ERROR: ไม่พบข้อมูล JSON (ตัวแปร json_data) ในหน้าเว็บ")
            return None
            
        json_string = match.group(1)
        data = json.loads(json_string)
        
        water_storage = data[0]['itc_water']['C13']['storage']
        if water_storage:
            value = float(str(water_storage).replace(',', ''))
            print(f"✅ พบข้อมูลเขื่อนเจ้าพระยา: {value}")
            return value
            
    except Exception as e:
        print(f"❌ ERROR: fetch_chao_phraya_dam_discharge: {e}")
        return None
    return None

# --- [ฟังก์ชันใหม่] ดึงข้อมูลย้อนหลัง ---
def get_historical_dam_data(file_path: str):
    try:
        if not os.path.exists(file_path):
            print(f"⚠️ ไม่พบไฟล์ข้อมูลย้อนหลังที่: {file_path}")
            return None

        df = pd.read_csv(file_path)
        df['date'] = pd.to_datetime(df['date'])
        
        today = datetime.now(pytz.timezone('Asia/Bangkok'))
        last_year_date = today.replace(year=today.year - 1)
        
        # ค้นหาข้อมูลที่ใกล้วันนี้ในปีที่แล้วที่สุด
        last_year_data = df.iloc[(df['date'] - last_year_date).abs().argsort()[:1]]
        
        if not last_year_data.empty:
            historical_discharge = last_year_data['discharge_rate'].iloc[0]
            print(f"✅ พบข้อมูลย้อนหลัง ({last_year_date.strftime('%Y-%m-%d')}): {historical_discharge}")
            return historical_discharge
        return None
    except Exception as e:
        print(f"❌ ERROR: get_historical_dam_data: {e}")
        return None

# --- วิเคราะห์และสร้างข้อความ (อัปเดต) ---
def analyze_and_create_message(inburi_level, dam_discharge, bank_height, historical_discharge=None):
    distance_to_bank = bank_height - inburi_level
    
    # เพิ่มส่วนแสดงข้อมูลย้อนหลัง
    historical_text = ""
    if historical_discharge is not None:
        historical_text = f"\n  (เทียบปีที่แล้ว: {historical_discharge:,.0f} ลบ.ม./วินาที)"

    # ตรรกะการเตือนภัยคงเดิม
    if dam_discharge > 2400 or distance_to_bank < 1.0:
        status_emoji = "🟥"
        status_title = "‼️ ประกาศเตือนภัยระดับสูงสุด ‼️"
        recommendation = "คำแนะนำ:\n1. เตรียมพร้อมอพยพหากอยู่ในพื้นที่เสี่ยง\n2. ขนย้ายทรัพย์สินขึ้นที่สูงโดยด่วน\n3. งดใช้เส้นทางสัญจรริมแม่น้ำ"
    elif dam_discharge > 1800 or distance_to_bank < 2.0:
        status_emoji = "🟨"
        status_title = "‼️ ประกาศเฝ้าระวัง ‼️"
        recommendation = "คำแนะนำ:\n1. บ้านเรือนริมตลิ่งนอกคันกั้นน้ำ ให้เริ่มขนของขึ้นที่สูง\n2. ติดตามสถานการณ์อย่างใกล้ชิด"
    else:
        status_emoji = "🟩"
        status_title = "สถานะปกติ"
        recommendation = "สถานการณ์น้ำยังปกติ ใช้ชีวิตได้ตามปกติครับ"

    now = datetime.now(pytz.timezone('Asia/Bangkok'))
    message = (
        f"{status_emoji} {status_title}\n"
        f"รายงานสถานการณ์น้ำเจ้าพระยา อ.อินทร์บุรี\n"
        f"ประจำวันที่: {now.strftime('%d/%m/%Y %H:%M')} น.\n\n"
        f"• ระดับน้ำ (อินทร์บุรี): {inburi_level:.2f} ม.รทก.\n"
        f"  (ต่ำกว่าตลิ่งประมาณ {distance_to_bank:.2f} ม.)\n"
        f"  (ระดับตลิ่ง: {bank_height:.2f} ม.รทก.)\n"
        f"• เขื่อนเจ้าพระยา (ข้อมูลอ้างอิง): {dam_discharge:,.0f} ลบ.ม./วินาที{historical_text}\n\n" # <-- เพิ่มข้อมูลย้อนหลังตรงนี้
        f"{recommendation}"
    )
    return message

def create_error_message(inburi_status, discharge_status):
    now = datetime.now(pytz.timezone('Asia/Bangkok'))
    return (
        f"⚙️❌ เกิดข้อผิดพลาดในการดึงข้อมูล ❌⚙️\n"
        f"เวลา: {now.strftime('%d/%m/%Y %H:%M')} น.\n\n"
        f"• สถานะข้อมูลระดับน้ำอินทร์บุรี: {inburi_status}\n"
        f"• สถานะข้อมูลเขื่อนเจ้าพระยา: {discharge_status}\n\n"
        f"กรุณาตรวจสอบ Log บน GitHub Actions เพื่อดูรายละเอียดข้อผิดพลาดครับ"
    )

def send_line_broadcast(message):
    if not LINE_TOKEN:
        print("❌ ไม่พบ LINE_CHANNEL_ACCESS_TOKEN!")
        return
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_TOKEN}"
    }
    payload = {"messages": [{"type": "text", "text": message}]}
    try:
        res = requests.post(LINE_API_URL, headers=headers, json=payload, timeout=10)
        res.raise_for_status()
        print("✅ ส่งข้อความ Broadcast สำเร็จ!")
    except Exception as e:
        print(f"❌ ERROR: LINE Broadcast: {e}")

# --- Main (อัปเดต) ---
if __name__ == "__main__":
    print("=== เริ่มการทำงานระบบแจ้งเตือนน้ำอินทร์บุรี (Final Version) ===")
    
    inburi_level, bank_level = get_inburi_data(SINGBURI_URL)
    dam_discharge = fetch_chao_phraya_dam_discharge(DISCHARGE_URL)
    historical_discharge = get_historical_dam_data(HISTORICAL_DATA_FILE)

    if inburi_level is not None and bank_level is not None and dam_discharge is not None:
        final_message = analyze_and_create_message(inburi_level, dam_discharge, bank_level, historical_discharge)
    else:
        inburi_status = f"สำเร็จ" if inburi_level is not None else "ล้มเหลว"
        discharge_status = f"สำเร็จ" if dam_discharge is not None else "ล้มเหลว"
        final_message = create_error_message(inburi_status, discharge_status)

    print("\n📤 ข้อความที่จะแจ้งเตือน:")
    print(final_message)
    print("\n🚀 ส่งข้อความไปยัง LINE...")
    send_line_broadcast(final_message)
    print("✅ เสร็จสิ้นการทำงาน")
