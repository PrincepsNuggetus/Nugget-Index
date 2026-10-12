# Nugget Index 设置说明

按顺序做。第 1、2 步现在就能做；第 3 步等 IBKR 开户后、**第一笔交易之前**做。

---

## 第 1 步：设置数据密码（现在做）

仓库是公开的，原始交易数据会先加密再保存。密码由你自己定，只存在 GitHub 的加密设置和你的密码管理器里。

1. 打开 https://github.com/PrincepsNuggetus/Nugget-Index/settings/secrets/actions
2. 点 **New repository secret**
3. Name 填 `NUGGET_DATA_KEY`
4. Secret 填一个你自己编的长密码，建议 5 个以上随机英文单词，例如 `orbit tulip granite seventy lantern`（别用这个例子）
5. 点 **Add secret**

**这个密码一定要另外存一份**（密码管理器、或写在纸上收好）。丢了它，之前加密的原始数据就打不开了，每日任务也会停。

## 第 2 步：打开网页（现在做）

1. 打开 https://github.com/PrincepsNuggetus/Nugget-Index/settings/pages
2. **Build and deployment → Source** 选 **GitHub Actions**
3. 打开 https://github.com/PrincepsNuggetus/Nugget-Index/actions ，左边点 **Daily update**，右边点 **Run workflow** → **Run workflow**
4. 一两分钟后打开 https://princepsnuggetus.github.io/Nugget-Index/ ，能看到演示页面就成功了

在接入 IBKR 之前，页面显示的是演示数据，右上角有 "Demo data" 标记。

---

## 第 3 步：接入 IBKR（开户后、第一笔交易前做）

### 3a. 建报表模板（Flex Query）

1. 登录 IBKR Client Portal → **Performance & Reports → Flex Queries**
2. 在 **Activity Flex Query** 旁边点 **+** 新建，名字填 `Nugget Index`
3. 勾选下面 4 个部分（Sections），每个部分里的字段直接点 **Select All** 最省事：

| Section | 选项（Options） |
| --- | --- |
| **Net Asset Value (NAV) in Base** | 默认 |
| **Cash Transactions** | 勾选 Deposits & Withdrawals（其他可以都勾） |
| **Trades** | 选 **Execution** |
| **Open Positions** | 选 **Summary** |

4. 底部 **Delivery Configuration**：
   - Format：**XML**
   - Period：**Last 365 Calendar Days**
5. **General Configuration**：
   - Date Format：**yyyyMMdd**
   - Time Format：**HHmmss**
   - Date/Time Separator：**; (semi-colon)**
   - Breakout by Day：**Yes**
   - 其他保持默认
6. 保存。回到 Flex Queries 列表，点这个模板旁边的 **ⓘ 图标**，记下 **Query ID**（一串数字）

### 3b. 生成令牌（Token）

1. Client Portal → **Settings → Account Report → Flex Web Service**
2. 勾选启用，点 **Generate New Token**
3. **Expire After** 选 **1 year**；**Valid for IP Address 留空**（GitHub 的服务器 IP 会变）
4. 保存，复制显示的令牌数字

这个令牌只能下载报表，不能交易，也不是你的登录密码。

### 3c. 把令牌和 Query ID 放进 GitHub

打开 https://github.com/PrincepsNuggetus/Nugget-Index/settings/secrets/actions ，再加两个 secret：

| Name | Secret |
| --- | --- |
| `IBKR_FLEX_TOKEN` | 3b 里的令牌 |
| `IBKR_FLEX_QUERY_ID` | 3a 里的 Query ID |

### 3d. 跑第一次

1. 入金后、买入任何股票之前，去 Actions → **Daily update** → **Run workflow**
2. 跑完后开个 chat 告诉 Claude："检查一下 Nugget-Index 第一次跑的结果"。Claude 会核对报表字段是否齐全，有缺的会告诉你在 IBKR 里补哪一项

之后每个交易日夜里自动运行，你不用管。

---

## 日常

- **交易**：正常在 IBKR 下单，什么都不用做。
- **加交易笔记（可选）**：开个 chat 跟 Claude 说"给 2026-11-03 买的 NVDA 加笔记：……"，Claude 会写进 `data/notes.json`。
- **改页面设计或加功能**：开个 chat，说"打开我的 GitHub 仓库 PrincepsNuggetus/Nugget-Index"，然后说要改什么。
- **每年一次**：IBKR 令牌到期前重新生成，替换 GitHub 里的 `IBKR_FLEX_TOKEN`。

## 准备公开时

- 把网址发出去就行，历史记录和时间戳从第一天起就在。
- 想让别人核对原始数据时，可以公开 `NUGGET_DATA_KEY`，或者让 Claude 把某段时间的原始报表解密后单独发布。
- 如果想让交易延迟显示（比如 30 天后才出现在页面上），告诉 Claude 把 `config.json` 里的 `trade_delay_days` 改成 30。延迟只针对交易记录，当前持仓仍按当天显示；加密数据和时间戳不受影响。
