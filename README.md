# DXB → China 机票监控 ✈️🇨🇳

基于 [rizabalci/worldwide-flight-bot](https://github.com/rizabalci/worldwide-flight-bot)（MIT License）改造而来，专门监控 **从迪拜 (DXB) 到中国 22 个主要城市** 的往返机票价格，发现低价自动通过 Telegram 推送。

零成本运行在 GitHub Actions 上，无需服务器。

## ✨ 核心特性

- 🎯 **每个城市独立目标价**——HKG ¥1800 起步，PEK/PVG ¥2700，西北/西南 ¥3200
- 📉 **3 种触发条件**：跌破目标价 / 14 天均价下跌 ≥20% / 历史新低
- 🇨🇳 **报价用 CNY 显示**（可在 `.env` 改成 AED / USD / EUR 等）
- 📊 **价格历史自动 commit 回仓库**——为下次扫描提供 baseline
- 📬 **Telegram 推送**——分三类（all-time low / 大跌 / 跌破目标）
- 💓 **静默日发心跳**——确保 bot 没静默挂掉
- 🆓 **完全免费**——Travelpayouts API + GitHub Actions + Telegram Bot

## 📦 文件清单

```
flight-monitor-dxb-cn/
├── .github/workflows/check.yml   # 每天 07:00 UTC (11:00 Dubai / 15:00 CN) 跑
├── check_flights.py              # 主程序，单文件 770 行
├── requirements.txt              # 依赖：requests
├── .env.example                  # 配置模板
├── .gitignore
└── README.md
```

## 🚀 三步部署

### 第一步：拿 Travelpayouts API Token（5 分钟）

1. 打开 https://www.travelpayouts.com/ 用邮箱注册
2. 进入 **API** → **Data API** → 拿 token
3. **免费额度**：每月 1500~5000 次（取决于套餐），DXB→22 城市 × 6 个月 ≈ 132 次/天 = 3960/月

> 注：Travelpayouts 的 Data API 用的是 Aviasales 缓存，**数据延迟 4~12 小时**，但完全免费且免审批

### 第二步：创建 Telegram Bot（3 分钟）

1. 在 Telegram 里 @ **@BotFather**
2. 发 `/newbot`，按提示给名字，得到 `BOT_TOKEN`
3. 自己先给机器人发一条消息激活对话
4. 浏览器打开 `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates` 拿到自己的 `chat_id`
   - 或者用 @userinfobot 也行

### 第三步：部署到 GitHub（5 分钟）

```bash
cd flight-monitor-dxb-cn
git init && git add . && git commit -m "init"
git branch -M main
git remote add origin git@github.com:<你的用户名>/flight-monitor-dxb-cn.git
git push -u origin main
```

然后到仓库 **Settings → Secrets and variables → Actions** 加 secrets：

| Secret | 必填 | 说明 |
|---|---|---|
| `TRAVELPAYOUTS_TOKEN` | ✅ | 上面拿到的 token |
| `TELEGRAM_BOT_TOKEN` | ✅ | BotFather 给的 token |
| `TELEGRAM_CHAT_ID` | ✅ | 你的 chat_id |

然后到 **Variables** 标签页加（可选，全用默认值也行）：

| Variable | 默认 | 说明 |
|---|---|---|
| `CURRENCY` | `cny` | 报价货币：`cny`/`aed`/`usd`/`eur` 等 |
| `ORIGINS` | `DXB` | 出发地 IATA，可多选 `DXB,AUH` |
| `MONTHS_AHEAD` | `6` | 搜索未来几个月 |
| `MAX_NIGHTS` | `21` | 单次旅行最长天数 |
| `WATCHLIST` | `PEK,PVG,HKG` | 每天显示这些航线的当前价 |
| `ROLLING_WINDOW_DAYS` | `14` | 滚动均价窗口 |
| `ROLLING_DROP_PCT` | `0.20` | 跌幅阈值 |
| `TARGET_MARGIN_PCT` | `0.10` | 低于目标多少才算"跌破" |
| `HEARTBEAT` | `true` | 没 deal 时是否发心跳 |
| `MUTE` | `false` | 静默模式：跑但不推送 |
| `BROAD_FALLBACK` | `true` | 当月没数据时是否补一次广搜 |

最后进 **Actions** 标签页 → 启用 workflow → 手动 Run workflow 一次试试。

## 📱 推送样例

```
🇨🇳 DXB → China deal scan · 30 Jul
   round-trip · 1 adult · currency CNY

🏆 All-time lows

Hong Kong · 香港  DXB→  ¥1,750  〰️ avg ¥2,100  (target ¥1,800)  · low ¥1,820
   28 Sep → 05 Oct  (7n)  · CX  · book
   🇨🇳 best season: Oct–Dec (mild, dry)

💰 Below target

Guangzhou · 广州  DXB→  ¥2,150  📉 avg ¥2,500  (target ¥2,400)
   15 Oct → 22 Oct  (7n)  · EK  · book
   🇨🇳 best season: Oct–Mar (cool, dry)
```

```
👀 Watching · 30 Jul

Beijing Capital · 北京首都  DXB→  ¥2,650  📉 avg ¥2,900  (target ¥2,700)
   10 Nov → 17 Nov  (7n)  · CA
   🇨🇳 best season: Sep–Oct (autumn, clear skies)
```

## ⚙️ 调目标价

打开 `check_flights.py` 找 `CHINA_DESTINATIONS`，每个城市的元组第二项就是目标价（CNY 往返）：

```python
CHINA_DESTINATIONS = {
    "HKG": ("Hong Kong · 香港", 1800),    # ← 把 1800 改大 = 减少触发
    "PEK": ("Beijing Capital · 北京首都", 2700),  # ← 改小 = 更频繁告警
    ...
}
```

想加新城市？找到对应 IATA 代码加进去就行。常用城市代码：

| 城市 | IATA |
|---|---|
| 北京首都 | `PEK` |
| 北京大兴 | `PKX` |
| 上海浦东 | `PVG` |
| 上海虹桥 | `SHA` |
| 杭州 | `HGH` |
| 南京 | `NKG` |
| 厦门 | `XMN` |
| 青岛 | `TAO` |
| 长沙 | `CSX` |
| 郑州 | `CGO` |
| 沈阳 | `SHE` |
| 哈尔滨 | `HRB` |
| 兰州 | `LHW` |
| 乌鲁木齐 | `URC` |
| 拉萨 | `LXA` |

加完 push 到 GitHub，下次跑就会带上。

## 🔄 想加出发地？

默认 `ORIGINS=DXB`。可以加 `ORIGINS=DXB,AUH` 同时监控阿布扎比起飞，但 AUH 数据稀疏，可能大多数查不到（Travelpayouts 数据偏向热门出发地）。

## 🛠 常见问题

**Q: 跑完没收到推送？**
A: 第一次跑通常没历史数据，**不会触发任何 deal**（因为没有 baseline）。跑 3~5 天后历史数据积累起来，"big drop" 和 "all-time low" 才会触发。要立刻收到推送，把目标价暂时调大再跑一次。

**Q: 想每天跑多次？**
A: 改 `.github/workflows/check.yml` 的 cron，比如 `0 7,19 * * *` 是每天两次。但注意：免费 token 通常每月 1500~2000 次。

**Q: 想要微信推送？**
A: 把 `send_telegram` 函数替换成 PushPlus / Server酱 的 HTTP 调用，逻辑不变。或者搭一个简单的转发：telegram bot 收到消息后转发到微信（用 itchat 等库）。

**Q: 想看价格历史？**
A: 仓库里有个 `price_history.json`，每次跑都更新。可以本地用 Python 画图：
```python
import json, matplotlib.pyplot as plt
data = json.load(open("price_history.json"))
for k, v in data.items():
    prices = [p["price"] for p in v["series"]]
    plt.plot(prices, label=k)
plt.legend(); plt.show()
```

**Q: 报错 `429 Too Many Requests`？**
A: Travelpayouts 免费层是 60 次/分钟。脚本已经会自动重试，但持续高并发会被限速。把 `PACING_SECONDS` 调到 1.0 试试。

**Q: 想监控的不是迪拜？**
A: 改 `ORIGINS` 变量（IATA 代码），同时调目标价。原项目 `rizabalci/worldwide-flight-bot` 是维也纳版，监控欧洲 + 全球。

## 🆚 与原项目的差异

| | 原项目 (rizabalci) | 本项目 (DXB→CN) |
|---|---|---|
| 出发地 | VIE, BTS（维也纳/布拉迪斯拉发） | DXB（迪拜） |
| 目的地 | 596 个城市（欧洲 + 全球） | 22 个中国城市 |
| Tier | 2 个（短途/长途） | 1 个（中国长途） |
| 货币 | EUR | CNY（可改） |
| 推送 emoji | 🇪🇺 🌍 | 🇨🇳 |
| Dashboard | 有（HTML + GitHub Pages） | 无（Telegram 即 UI） |
| License | MIT | MIT |
| License 上游 | – | rizabalci/worldwide-flight-bot |

## 📜 License

MIT (继承自上游项目)