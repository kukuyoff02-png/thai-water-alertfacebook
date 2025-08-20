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
# --- [ใหม่] ดึงค่า Webhook URL จาก GitHub Secrets ---
MAKE_WEBHOOK_URL = os.environ.get('MAKE_WEBHOOK_URL')

# -- อ่านข้อมูลย้อนหลังจาก Excel --
THAI_MONTHS = {
    'มกราคม':1, 'กุมภาพันธ์':2, 'มีนาคม':3, 'เมษายน':4,
    'พฤษภาคม':5, 'มิถุนายน':6, 'กรกฎาคม':7, 'สิงหาคม':8,
    'กันยายน':9, 'ตุลาคม':10, 'พฤศจิกายน':11, 'ธันวาคม':12
}
def get_historical_from_excel(year_be: int) -> int | None:
    path = f"data/ระดับน้ำปี{year_be}.xlsx"
    try:
        if not os.path.exists(path):
            print(f"⚠️ ไม่พบไฟล์ข้อมูลย้อนหลังที่: {path}")
            return None
        df = pd.read_excel(path)
        df = df.rename(columns={'ปริมาณน้ำ (ลบ.ม./วินาที)': 'discharge'})
        df['month_num'] = df['เดือน'].map(THAI_MONTHS)
        now = datetime.now(pytz.timezone('Asia/Bangkok'))
        today_d, today_m = now.day, now.month
        match = df[(df['วันที่']==today_d) & (df['month_num']==today_m)]
        if not match.empty:
            print(f"✅ พบข้อมูลย้อนหลังสำหรับปี {year_be}: {int(match.iloc[0]['discharge'])} ลบ.ม./วินาที")
            return int(match.iloc[0]['discharge'])
        else:
            print(f"⚠️ ไม่พบข้อมูลสำหรับวันที่ {today_d}/{today_m} ในไฟล์ปี {year_be}")
            return None
    except Exception as e:
        print(f"❌ ERROR: ไม่สามารถโหลดข้อมูลย้อนหลังจาก Excel ได้ ({path}): {e}")
        return None

# --- ดึงระดับน้ำอินทร์บุรี (ฉบับปรับปรุง) ---
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
            
            # --- [แก้ไข] เพิ่มการรอและรีเฟรชเพื่อให้ได้ข้อมูลล่าสุด ---
            print("⏳ รอหน้าเว็บโหลดข้อมูลเริ่มต้น...")
            WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.CSS_SELECTOR, "th[scope='row']")))
            
            print("🔄 ทำการรีเฟรชหน้าเว็บเพื่อดึงข้อมูลล่าสุด...")
            driver.refresh() # สั่งให้เบราว์เซอร์รีเฟรช 1 ครั้ง
            
            # รออีกครั้งหลังรีเฟรช และหน่วงเวลาเพิ่มเล็กน้อยให้ JavaScript ทำงาน
            print("⏳ รอข้อมูลหลังรีเฟรช และหน่วงเวลา 3 วินาที...")
            WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.CSS_SELECTOR, "th[scope='row']")))
            time.sleep(3) # หน่วงเวลาเพิ่ม 3 วินาที
            # --- จบส่วนที่แก้ไข ---
            
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
        except Exception as e:
            print(f"❌ ERROR: get_inburi_data: {e}")
            if driver: driver.quit()
            return None, None
    return None, None

# --- ดึงข้อมูลเขื่อนเจ้าพระยา ---
def fetch_chao_phraya_dam_discharge(url: str, timeout: int = 30):
    try:
        headers = {'User-Agent': 'Mozilla/5.0', 'Cache-Control': 'no-cache', 'Pragma': 'no-cache'}
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
            value = float(str(water_storage).replace(',', ''))
            print(f"✅ พบข้อมูลเขื่อนเจ้าพระยา: {value}")
            return value
    except Exception as e:
        print(f"❌ ERROR: fetch_chao_phraya_dam_discharge: {e}")
    return None

# --- วิเคราะห์และสร้างข้อความ ---
def analyze_and_create_message(inburi_level, dam_discharge, bank_height, hist_2567=None, hist_2554=None):
    distance_to_bank = bank_height - inburi_level
    ICON, HEADER, summary_text = "", "", ""
    if dam_discharge > 2400 or distance_to_bank < 1.0:
        ICON, HEADER = "🟥", "‼️ ประกาศเตือนภัยระดับสูงสุด ‼️"
        summary_text = "คำแนะนำ:\n1. เตรียมพร้อมอพยพหากอยู่ในพื้นที่เสี่ยง\n2. ขนย้ายทรัพย์สินขึ้นที่สูงโดยด่วน\n3. งดใช้เส้นทางสัญจรริมแม่น้ำ"
    elif dam_discharge > 1800 or distance_to_bank < 2.0:
        ICON, HEADER = "🟨", "‼️ ประกาศเฝ้าระวัง ‼️"
        summary_text = "คำแนะนำ:\n1. บ้านเรือนริมตลิ่งนอกคันกั้นน้ำ ให้เริ่มขนของขึ้นที่สูง\n2. ติดตามสถานการณ์อย่างใกล้ชิด"
    else:
        ICON, HEADER = "🟩", "สถานะปกติ"
        summary_text = "สถานการณ์น้ำยังปกติ ใช้ชีวิตได้ตามปกติครับ"
    now = datetime.now(pytz.timezone('Asia/Bangkok'))
    TIMESTAMP = now.strftime('%d/%m/%Y %H:%M')
    msg_lines = [
        f"{ICON} {HEADER}", "",
        f"📍 รายงานสถานการณ์น้ำเจ้าพระยา จ.อ.อินทร์บุรี", f"🗓️ วันที่: {TIMESTAMP} น.", "",
        "🌊 ระดับน้ำ + ระดับตลิ่ง",
        f"  • อินทร์บุรี: {inburi_level:.2f} ม.รทก.", f"  • ตลิ่ง: {bank_height:.2f} ม.รทก. (ต่ำกว่า {distance_to_bank:.2f} ม.)", "",
        "💧 ปริมาณน้ำปล่อยเขื่อนเจ้าพระยา", f"  {dam_discharge:,} ลบ.ม./วินาที", "",
        "🔄 เปรียบเทียบย้อนหลัง",
    ]
    if hist_2567 is not None: msg_lines.append(f"  • ปี 2567: {hist_2567:,} ลบ.ม./วินาที")
    if hist_2554 is not None: msg_lines.append(f"  • ปี 2554: {hist_2554:,} ลบ.ม./วินาที")
    msg_lines += ["", summary_text]
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

# --- [ใหม่] ฟังก์ชันสำหรับส่งข้อมูลไปที่ Make.com Webhook ---
def send_to_make_webhook(message: str):
    if not MAKE_WEBHOOK_URL:
        print("❌ ไม่พบ MAKE_WEBHOOK_URL! กรุณาตั้งค่าใน GitHub Secrets")
        return
    payload = {"message": message}
    headers = {"Content-Type": "application/json"}
    try:
        res = requests.post(MAKE_WEBHOOK_URL, headers=headers, json=payload, timeout=20)
        res.raise_for_status()
        if res.text == "Accepted":
             print(f"✅ ส่งข้อมูลไปที่ Make.com Webhook สำเร็จ!")
        else:
             print(f"⚠️ Webhook ตอบกลับมาว่า: {res.text}")
    except requests.exceptions.RequestException as e:
        print(f"❌ ERROR: ไม่สามารถส่งข้อมูลไปที่ Webhook ได้: {e}")

# --- Main ---
if __name__ == "__main__":
    print("=== เริ่มการทำงานระบบแจ้งเตือนน้ำอินทร์บุรี ===")
    inburi_level, bank_level = get_inburi_data(f"{SINGBURI_URL}?cb={random.randint(10000, 99999)}")
    dam_discharge = fetch_chao_phraya_dam_discharge(DISCHARGE_URL)
    hist_2567 = get_historical_from_excel(2567)
    hist_2554 = get_historical_from_excel(2554)
    if inburi_level is not None and bank_level is not None and dam_discharge is not None:
        final_message = analyze_and_create_message(inburi_level, dam_discharge, bank_level, hist_2567, hist_2554)
    else:
        inburi_status = "สำเร็จ" if inburi_level is not None else "ล้มเหลว"
        discharge_status = "สำเร็จ" if dam_discharge is not None else "ล้มเหลว"
        final_message = create_error_message(inburi_status, discharge_status)
    print("\n📤 ข้อความที่จะส่ง:")
    print(final_message)
    # --- [เปลี่ยน] เรียกใช้ฟังก์ชันใหม่เพื่อส่งไปที่ Make.com ---
    print("\n🚀 ส่งข้อมูลไปยัง Make.com Webhook...")
    send_to_make_webhook(final_message)
    print("✅ เสร็จสิ้นการทำงาน")
