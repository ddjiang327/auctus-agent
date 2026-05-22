# Auctus Agent — Marketing Plan

## 一、产品定位

**核心主张（一句话）**

> 你的本地 AI 助手，越用越懂你，数据永远在自己手里。

**英文版**

> Your personal AI that learns from you — runs local, stays private, evolves with you.

**差异化优势（vs 竞品）**

| 竞品 | 他们有什么 | Auctus 多了什么 |
|---|---|---|
| Claude Desktop / ChatGPT | 云端对话 | 本地执行、记忆、定时、自我进化 |
| Jan.ai / LM Studio | 本地模型运行 | 完整 Agent 能力（任务/记忆/浏览器/Cron） |
| Open Interpreter | 代码执行 | 非技术友好 UI、记忆闭环、移动配对 |
| Cursor | 编程专用 | 通用助手，文件/邮件/分析/调度全覆盖 |

---

## 二、目标用户

**主要人群**
- 个人效率用户（35%）：独立创作者、自由职业者、研究员
- 技术用户（40%）：开发者、数据分析师、AI 爱好者
- 隐私敏感用户（25%）：不愿数据上云的专业人士

**中国重点人群**
- 正在用 DeepSeek API 的用户
- 独立开发者 / 个人 SaaS 创始人
- 知识工作者（咨询、财务、法律）

---

## 三、中国市场策略

### 3.1 渠道优先级

| 渠道 | 定位 | 内容类型 |
|---|---|---|
| **GitHub** | 获取开发者用户 | README、Demo GIF、中文文档 |
| **V2EX** | 技术圈口碑 | "我做了个…" 帖子，诚恳分享 |
| **知乎** | 长尾搜索流量 | 深度文章：「本地 AI Agent 怎么选」 |
| **小红书** | 非技术用户种草 | 图文笔记：「这个工具让我的效率翻倍」 |
| **B站** | 视频演示 | 5–10分钟 demo 视频 |
| **微信** | 私域留存 | 公众号 + 用户交流群 |
| **即刻** | 早期种子用户 | 产品更新动态、使用技巧 |

### 3.2 内容策略（中文）

**主题一：隐私牌**
> "为什么我的 AI 助手不能碰我的文件？——我做了个跑在本地的 Agent"

**主题二：DeepSeek 集成**
> "DeepSeek API + 本地 Agent = 月均成本 ¥20 的个人助理"

**主题三：自我进化**
> "我用了三个月，我的 AI 助手越来越懂我是怎么做到的"

**主题四：对比测评**
> "本地 AI Agent 横评：Jan.ai vs Open Interpreter vs Auctus Agent"

### 3.3 社区操作（具体行动）

1. **V2EX 首发帖** — 「Show V2EX：我做了个本地 AI Agent，支持 DeepSeek，数据不出机器」
2. **知乎专栏** — 3 篇系列文章（产品介绍 → 使用教程 → 背后设计理念）
3. **小红书** — 每周 2–3 条图文，展示具体使用场景（写报告、整理邮件、定时任务）
4. **B站 Demo 视频** — Task Mode 演示、自我学习演示、移动配对演示
5. **微信用户群** — 在第一批用户中建群，收集反馈，打磨产品

---

## 四、国际市场策略

### 4.1 渠道优先级

| 渠道 | 定位 | 内容类型 |
|---|---|---|
| **GitHub** | 核心阵地 | Stars、README、Releases |
| **Hacker News** | 技术圈引爆 | Show HN 帖 |
| **Product Hunt** | 产品曝光 | 正式发布，冲日榜 |
| **Reddit** | 社区口碑 | r/LocalLLaMA、r/selfhosted、r/productivity |
| **Twitter/X** | 实时传播 | Demo GIF、功能更新 |
| **YouTube** | 搜索长尾 | 教程视频、与竞品对比 |
| **Discord** | 用户留存 | LocalLLaMA、AI 开发者社区 |

### 4.2 内容策略（英文）

**主题一：Privacy angle**
> "I built a personal AI that never sends my files to the cloud"

**主题二：Self-evolving angle**
> "After 3 months, my AI assistant actually knows how I like to work"

**主题三：BYO Key angle**
> "Full AI agent capabilities for $5/month — bring your own API key"

**主题四：Competitor comparison**
> "Why I stopped using Claude Desktop and built my own local agent"

### 4.3 具体行动

1. **GitHub 优化**
   - 完善英文 README（Hero banner + GIF demo + one-liner install）
   - 申请 GitHub Star History badge
   - 写好 CONTRIBUTING.md，吸引开源贡献者

2. **Show HN 帖**（上线后第一周）
   - 标题：`Show HN: Auctus Agent – local AI agent that learns from you (Mac/Windows)`
   - 诚实介绍产品当前状态，重点讲 Closed Learning Loop（最差异化的功能）

3. **Product Hunt 发布**
   - 提前 2 周联系 Makers，准备 Hunter
   - 发布日提前在 Discord/Twitter 预热
   - 目标：当日 Top 5

4. **Reddit 策略**
   - r/LocalLLaMA：技术深度帖，讲 Closed Learning Loop 的实现
   - r/selfhosted：隐私 + 本地运行角度
   - r/productivity：场景化使用 demo（不要硬广）

---

## 五、Launch Sequence（冷启动时间轴）

```
Week 1   → GitHub README 优化 + Demo GIF 制作
Week 2   → V2EX 中文首发帖 + Twitter 账号激活
Week 3   → B站/小红书第一波内容 + Reddit 首帖
Week 4   → Show HN 正式发布
Week 6   → Product Hunt 发布（冲榜）
Month 2+ → 持续内容输出 + 用户案例 UGC
```

---

## 六、核心 Demo 内容（必须做）

这是一切的基础，没有这个其他渠道都没法推：

1. **60秒 GIF Demo** — Task Mode 从输入到完成一个复杂任务
2. **「自我学习」演示视频** — 展示 Closed Learning Loop 闭环过程（这是最独特的功能，没有竞品做到这个程度）
3. **一键安装流程截图** — 非技术用户看到要有信心
4. **使用场景截图集** — 6–8 个具体场景（分析财报、整理邮件、定时生成报告…）

---

## 七、关键指标

| 阶段 | 目标 |
|---|---|
| Month 1 | GitHub 500 Stars，V2EX/HN 获得真实反馈 |
| Month 2 | 100 活跃用户，建立用户群 |
| Month 3 | 1000 Stars，Product Hunt 发布 |
| Month 6 | 5000 Stars，月活 500+，形成用户自发传播 |

---

## 八、注意事项 / 风险

- **不要过早商业化**：现阶段应以口碑和社区为主，付费功能（多用户档案等）等产品稳定后再推
- **中国渠道要分开运营**：内容风格和技术深度与国际渠道不同，不要机器翻译混用
- **Closed Learning Loop 是护城河**，要重点讲，竞品做不到这种「越用越懂你」的闭环

---

_Last updated: 2026-05-22_
