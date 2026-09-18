import re
from pathlib import Path
import csv

# ====== 你的根目錄 ======
root_dir = Path("/workspaces/2025/gem5-project/m5out/")

# ====== 儲存所有 run 的紀錄 ======
records = []

# ====== 自動掃描每個資料夾 ======
for folder in root_dir.iterdir():
    if not folder.is_dir():
        continue

    config_path = folder / "config_used.txt"
    stats_path = folder / "stats.txt"

    if not config_path.exists() or not stats_path.exists():
        continue

    print(f"\n📁 處理資料夾: {folder.name}")

    # =============================
    # 解析 config_used.txt
    # =============================
    params = {}
    cost_items = {}

    with config_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            # ----- 參數 (Wf, Wd, ROB, cores...)
            m_param = re.match(r"(\w+)\s*=\s*([0-9.]+)", line)
            if m_param:
                key, val = m_param.groups()
                params[key] = float(val)
                continue

            # ----- cost breakdown (CPU_cost, L1_cost, ...)
            m_cost = re.match(r"(\w+_cost)\s*=\s*([0-9.]+)", line)
            if m_cost:
                key, val = m_cost.groups()
                cost_items[key] = float(val)
                continue

            # ----- Total Cost (rounded)
            m_total = re.match(r"Total\(rounded\)\s*=\s*([0-9.]+)", line)
            if m_total:
                cost_items["Total_cost"] = float(m_total.group(1))

    # =============================
    # 解析 stats.txt (只抓第一個 simSeconds)
    # =============================
    sim_seconds = None

    with stats_path.open("r", encoding="utf-8") as f:
        for line in f:
            if "simSeconds" in line:
                try:
                    val = float(line.split()[1])
                    if sim_seconds is None:    # 只取第一個
                        sim_seconds = val
                except:
                    pass

    if sim_seconds is None:
        print("⚠️ 找不到 simSeconds")
        continue

    print(f"  ✔ simSeconds(ROI) = {sim_seconds}")

    # =============================
    # 建立紀錄表
    # =============================
    rec = {"Folder": folder.name, "simSeconds": sim_seconds}

    # 加入所有參數
    for k, v in params.items():
        rec[k] = v

    # 加入所有 cost 欄位
    for k, v in cost_items.items():
        rec[k] = v

    records.append(rec)

# =============================
# 輸出 CSV（依 run 編號排序）
# =============================
if records:
    # ---- 依 run 編號排序 ----
    def folder_key(rec):
        m = re.search(r"run(\d+)", rec["Folder"])
        return int(m.group(1)) if m else 999999

    records = sorted(records, key=folder_key)

    # ---- 收集 CSV 欄位 ----
    fieldnames = sorted({key for r in records for key in r.keys()},
                        key=lambda x: (x != "Folder", x))

    output_path = root_dir / "auto_results.csv"
    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    print(f"\n✅ 已產生 auto_results.csv（已依 run 編號排序） → 共 {len(records)} 筆資料")

    # ---- 找出最快（simSeconds 最小）----
    best = min(records, key=lambda r: r["simSeconds"])
    print("\n🔥 最快的配置如下：")
    print(f"📁 Folder: {best['Folder']}")
    print(f"⏱ simSeconds: {best['simSeconds']}")

else:
    print("⚠️ 沒找到任何 run 的統計資料")

