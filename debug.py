import requests
from bs4 import BeautifulSoup

# --- ค่าคงที่ ---
# ลองกับเว็บข้อมูลเขื่อนที่ง่ายกว่าก่อน
URL_TO_DEBUG = "https://tiwrm.hii.or.th/DATA/REPORT/php/chart/chaopraya/small/chaopraya.php"

def download_and_inspect_page(url):
    print(f"🕵️ กำลังดาวน์โหลดข้อมูลจาก: {url}")
    try:
        res = requests.get(url, timeout=30)
        res.raise_for_status()
        
        # บันทึก HTML ทั้งหน้าลงไฟล์ เพื่อให้เราเปิดดูได้ง่ายๆ
        file_name = "debug_page.html"
        with open(file_name, "w", encoding="utf-8") as f:
            f.write(res.text)
        print(f"✅ ดาวน์โหลดสำเร็จ! บันทึกเนื้อหาเว็บลงในไฟล์ '{file_name}' แล้ว")
        print(f"👉 เปิดไฟล์ '{file_name}' และเปิดเว็บจริงในเบราว์เซอร์เพื่อเทียบกัน")

    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาดระหว่างดาวน์โหลด: {e}")

if __name__ == "__main__":
    download_and_inspect_page(URL_TO_DEBUG)
