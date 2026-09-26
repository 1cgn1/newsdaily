# 跨媒体新闻日报：Debian 13 VPS 部署与运维

本文介绍 Debian 13 VPS（已预装 `sudo`、`git`）上的一键安装、手工分步安装、配置、验证、邮件投递和回滚。所有真实凭据只通过 root 权限下的隐藏交互录入；文档和命令从不包含真实密钥。

## 部署前提与目录

部署目录固定为 `/opt/newsdaily`。应用以无登录系统账号 `newsdaily` 运行，程序、配置模板及 venv 由 root 管理；该服务账号只需写入项目的 `data/`、`output/`。系统级非敏感配置位于 `/etc/newsdaily/config`，秘密凭据位于 `/etc/newsdaily/credentials/`。SQLite 数据库为 `/opt/newsdaily/data/news.sqlite3`。

下方命令检查系统并创建上传目录；随后将本交付目录源码上传至 `/opt/newsdaily`。不要上传本机 `.env`、真实凭据、`.venv`、数据库或 `output/` 内容。运行安装前应确认目录中有 `scripts/install_debian13.sh`、`requirements.lock`、`config/` 和 `systemd/`：

```bash
cat /etc/os-release
sudo install -d -o root -g root -m 0755 /opt/newsdaily
```

## 一键安装（推荐）

下方命令需在 VPS 的项目目录执行。安装脚本仅支持 Debian 13 和 `/opt/newsdaily`；会安装 Python、venv、pip 与 CA 证书，创建/复用 `newsdaily` 系统账号，建立 venv 并安装锁定依赖，复制非敏感配置模板（若目标已存在则保留），安装 systemd 单元、daemon-reload，并执行 `init-db`。现存数据库和配置不会覆盖；不同的既有 systemd 单元会先备份。**安装不启用任何 timer，也不发送邮件。**

```bash
cd /opt/newsdaily
sudo bash scripts/install_debian13.sh
```

## 手工分步安装

以下各步可替代一键安装。下方命令安装系统依赖、创建服务账号和目录、建立 venv、安装锁定依赖并检查依赖一致性：

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip ca-certificates
getent passwd newsdaily >/dev/null || sudo useradd --system --user-group --home-dir /opt/newsdaily --no-create-home --shell /usr/sbin/nologin newsdaily
sudo install -d -o root -g root -m 0755 /opt/newsdaily
sudo install -d -o newsdaily -g newsdaily -m 0700 /opt/newsdaily/data /opt/newsdaily/output
if [ ! -x /opt/newsdaily/.venv/bin/python ]; then sudo python3 -m venv /opt/newsdaily/.venv; fi
sudo /opt/newsdaily/.venv/bin/python -m pip install --disable-pip-version-check -r /opt/newsdaily/requirements.lock
sudo /opt/newsdaily/.venv/bin/python -m pip check
sudo chown -R root:root /opt/newsdaily/.venv
sudo chmod -R go-w /opt/newsdaily/.venv
```

若 venv 已存在，不要重复创建覆盖它；使用其 Python 执行 pip 安装/检查即可。下方命令创建系统配置与凭据目录，并只在配置不存在时从模板初始化。已有 `/etc/newsdaily/config` 不会被覆盖：

```bash
sudo install -d -o root -g root -m 0755 /etc/newsdaily
sudo install -d -o root -g root -m 0700 /etc/newsdaily/credentials
if [ ! -e /etc/newsdaily/config ]; then sudo install -o root -g root -m 0644 /opt/newsdaily/.env.example /etc/newsdaily/config; fi
```

下方命令安装全部 service/timer 单元并让 systemd 重新读取；如果手工部署时已有同名单元，先自行备份 `/etc/systemd/system/` 中相应文件：

```bash
sudo install -o root -g root -m 0644 /opt/newsdaily/systemd/*.service /etc/systemd/system/
sudo install -o root -g root -m 0644 /opt/newsdaily/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

如果一键脚本不能运行，下面是完全不依赖它的凭据录入步骤。先确保上方的 `0700 root:root` 凭据目录已创建，再执行下方命令；三条模型密钥命令只执行所选供应商对应的一条，输入会被隐藏。不要把密钥写在命令参数里：

```bash
cd /opt/newsdaily
sudo .venv/bin/python -B -m scripts.install_credential deepseek_api_key
# 或：sudo .venv/bin/python -B -m scripts.install_credential openai_api_key
# 或：sudo .venv/bin/python -B -m scripts.install_credential dashscope_api_key
sudo .venv/bin/python -B -m scripts.install_credential smtp_auth_code
sudo .venv/bin/python -B -m scripts.install_credential sender_email
sudo .venv/bin/python -B -m scripts.install_credential recipient_email
```

上述手工录入完成后，下方命令不依赖一键脚本，可初始化数据库并让 systemd 以正式服务环境验证凭据与所选模型配置：

```bash
cd /opt/newsdaily
sudo -u newsdaily .venv/bin/python -B -m app.cli init-db
sudo systemctl start newsdaily-check-credentials.service
sudo systemctl status newsdaily-check-credentials.service --no-pager
```

## 可维护配置

### `config/settings.json`

该 JSON 是应用级运行策略，不含秘密。编辑前备份，保持合法 JSON，修改后重新执行安全检查。以下为所有受支持的顶层参数及模型档案字段：

| 参数 | 含义 |
| --- | --- |
| `timezone` | 预留说明字段；目前日报日期由代码固定按北京时间计算，改此字段不会改变时区。 |
| `daily_send_hour` | 预留说明字段；当前定时发送时间由 `systemd/newsdaily-send.timer` 的 `OnCalendar` 决定，改此字段不会更改 timer。 |
| `article_window_hours` | 纳入日报的新闻发布时间回溯窗口（小时）。 |
| `daily_event_target_min` / `daily_event_target_max` | 每日报告目标事件数下限/上限；未达到下限或校验失败应停止，不应降低质量门槛来强行发送。 |
| `retention_articles_days` | 预留字段；当前没有自动清理文章的任务，改此值不会删除记录。 |
| `retention_digest_days` | 预留字段；当前没有自动清理日报的任务，改此值不会删除记录。 |
| `max_input_tokens` | 可修改的模型输入 token 限制，用于控制提交给模型的输入规模。 |
| `input_estimate_safety_factor` | 输入 token 估算的安全系数；留出误差余量。程序粗估中文每字 0.6 token、其他字符每字 0.3 token，不下载或调用模型分词器，因此不是精确计量。 |
| `model_provider` | 当前选择的供应商，只能是 `deepseek`、`openai` 或 `qwen`。 |
| `model_profiles.<provider>.model` | 该供应商实际调用的模型标识。 |
| `model_profiles.<provider>.context_tokens` | 该模型可修改的上下文 token 限制；实际输入与输出必须能容纳在上下文窗口内。 |
| `model_profiles.<provider>.max_output_tokens` | 单次模型请求可修改的最大输出 token 限制；达到限制可能导致无法形成有效日报，应用应依校验结果停止。 |
| `model_profiles.<provider>.timeout_seconds` | 模型 API 请求超时秒数。 |

现行代码不再包含计费预算限制、价格配置或基于价格的预检/估价；只按输入、输出及上下文 token 限制模型请求。Qwen 不需要价格字段。DeepSeek、OpenAI、Qwen 三种模型仍可选；切换 `model_provider` 后应录入对应供应商凭据并做凭据检查。API 实际是否计费仍取决于供应商账户与其当前政策，token 限制不是费用承诺。

模型请求参数、凭据名称、官方主机白名单和供应商输出硬上限集中在 `app/llm.py` 的供应商档案中。以后增加官方 OpenAI 格式模型时，应先核实其接口参数和上限，再增加档案、凭据映射与固定样例测试；仅在 JSON 中写入新名称不会自动开放未知服务地址。

下方命令编辑模型及运行策略。配置中不得放 API key、SMTP 授权码或邮箱秘密：

```bash
sudoedit /opt/newsdaily/config/settings.json
```

### `config/sources.json`

这是媒体来源池，顶层为对象数组。每项可能出现的可维护字段如下；某字段对某来源不适用时可以省略。修改来源后应重新初始化数据库以补入新来源，并通过预览验证实际抓取行为。

| 字段 | 含义 |
| --- | --- |
| `id` | 稳定且唯一的来源机器标识；修改可能影响已有数据库关联。 |
| `name` | 展示用媒体名称。 |
| `homepage` | 媒体主页/站点根地址。 |
| `region` | 来源所属地区或主要报道区域。 |
| `language` | 内容语言代码，如 `en`、`zh`。 |
| `organization_type` | 机构类型说明。 |
| `funding_background` | 资金/所有权背景说明（可选）。 |
| `perspective` | 报道视角或编辑筛选提示（可选）。 |
| `enabled` | 是否参与来源发现与采集。 |
| `pool_status` | 来源池状态，如 `active`、`inactive`、`candidate`；停用来源不应因主页可访问就自动恢复。 |
| `discovery_strategy` | 发现方式，如 `rss`、`section`、`rss_or_section` 或受限 RSS 策略；须符合采集器支持范围。 |
| `section_urls` | 可公开访问的栏目页地址数组。 |
| `feed_urls` | 官方 RSS/Atom feed 地址数组（可选）。 |
| `feed_content_only` | 是否仅使用 feed 实际提供的内容，不再抓取 feed 外文章正文（可选）。 |
| `notes` | 管理员维护备注。 |
| `original_pool` | 标记是否属于原始来源池，用于报告分类。 |
| `inactive_reason` | 停用原因与恢复前置条件（可选）。 |
| `candidate_reason` | 候选来源尚未启用的原因（可选）。 |
| `candidate_filter` | 来源专属文章筛选条件/说明（可选）。 |
| `last_validation` | 最近验证信息（可选，通常作为审核记录维护）。 |
| `validation` | 人工维护的验证/访问合规记录（可选）；仅用于说明审核结论，不代表绕过访问限制的许可。 |

采集器生成的报告字段（如 `status`、`diagnostics`、`article_errors`、`qualified_articles`、`robots_*`、`checked_at`、`examples`、`validation`）属于运行结果，不是来源配置项，不要复制回配置文件作为控制开关。只使用公开且获准访问的栏目/feed；遵守 robots、许可条款和站点限制，不规避 403、证书错误或访问控制。下方命令编辑来源清单：

```bash
sudoedit /opt/newsdaily/config/sources.json
```

### `/etc/newsdaily/config` 非敏感系统参数

此文件由 systemd 作为 `EnvironmentFile` 加载，只放连接端点和开关，不放秘密。维护项包括：`CREDENTIALS_MODE=file`（凭据必须来自 root-only 文件）；`DEEPSEEK_BASE_URL`、`OPENAI_BASE_URL`、`QWEN_BASE_URL`（供应商 API 基础地址）；`SMTP_HOST`（SMTP 主机）、`SMTP_PORT`（端口）、`MAIL_USE_SSL`（是否使用 SSL）。发件地址、收件地址及 SMTP 授权码不是此配置文件参数，应保存在凭据文件中。下方命令编辑该文件：

```bash
sudoedit /etc/newsdaily/config
```

## 凭据录入与安全检查

凭据录入脚本仅接受 root 执行，输入不回显并要求确认；它将值写入 `/etc/newsdaily/credentials/`，权限为 `0600 root:root`，目录应为 `0700 root:root`。将实际模型供应商对应的一条 API 凭据与 SMTP 授权码、发件邮箱、收件邮箱分别录入。下方命令中模型一栏只执行所选供应商对应的一行；输入密钥时不要粘贴到命令本身：

```bash
cd /opt/newsdaily
sudo bash scripts/install_debian13.sh --set-credential deepseek_api_key
# 或：sudo bash scripts/install_debian13.sh --set-credential openai_api_key
# 或：sudo bash scripts/install_debian13.sh --set-credential dashscope_api_key
sudo bash scripts/install_debian13.sh --set-credential smtp_auth_code
sudo bash scripts/install_debian13.sh --set-credential sender_email
sudo bash scripts/install_debian13.sh --set-credential recipient_email
```

上述凭据命令只有名称，没有密钥值；脚本会以隐藏输入方式读取并二次确认。不得将秘密放入 shell 参数、环境变量、配置 JSON、`.env`、systemd 单元、数据库、日志、工单或聊天。systemd 使用 `LoadCredential=` 将凭据提供给单次服务进程；未配置的其他模型使用非秘密占位符，程序会明确拒绝把占位符当作 API 密钥。只需录入当前所选模型的密钥，切换模型时需先录入对应密钥并重新执行 `--check`。root 和 VPS 管理员仍可能读取磁盘凭据，应限制管理员权限并保护备份。

下方命令以服务账号幂等初始化数据库、查看状态并进行部署前安全检查。安装脚本的 `--check` 会核对所选模型凭据、邮件凭据、目录/文件权限、依赖和数据库状态；检查结果不显示凭据值：

```bash
cd /opt/newsdaily
sudo -u newsdaily .venv/bin/python -B -m app.cli init-db
sudo -u newsdaily .venv/bin/python -B -m app.cli status
sudo bash scripts/install_debian13.sh --check
```

若检查失败，核对 `model_provider` 与录入的模型凭据名称对应，确认目录是 `0700 root:root`、文件是 `0600 root:root`，并确认非敏感配置正确；不要通过 `cat`、`env`、`systemctl show` 等方式输出秘密。

## Dry-run 与真实预览

`digest --dry-run` 使用内置样例离线生成演示文件，不联网、不调用模型、不发信；它不能代替真实日报验证。真实预览会访问公开来源并调用已选模型，可能产生供应商侧费用，但 SMTP 不会发送邮件。下方命令先跑离线 dry-run，再人工触发真实预览服务并查看状态、日志与生成文件：

```bash
cd /opt/newsdaily
sudo -u newsdaily .venv/bin/python -B -m app.cli digest --dry-run
sudo systemctl start newsdaily-preview.service
sudo systemctl status newsdaily-preview.service --no-pager
sudo journalctl -u newsdaily-preview.service -n 80 --no-pager
sudo -u newsdaily /bin/sh -c 'cd /opt/newsdaily && ls -lt output/digest-full-*.html'
```

上述真实预览命令不发送邮件。检查 `output/` 下 HTML/TXT、`coverage-report.json`、`full-collection-status.json` 及运行报告中的日期、来源、证据、覆盖率和正文；质量门槛未达成时不应绕过校验发送。预览文件和数据库均含业务资料，应限制读取并纳入受控备份。

## 手工测试发信

只有安全检查通过且人工审阅当日真实预览后，才进行一次真实测试投递。将命令中的日期换成当前北京时间 `YYYY-MM-DD` 格式；这是实际发信操作，会投递到录入的收件邮箱。下方命令发送并查看结果：

```bash
sudo systemctl start newsdaily-send@full-YYYY-MM-DD.service
sudo systemctl status newsdaily-send@full-YYYY-MM-DD.service --no-pager
sudo journalctl -u newsdaily-send@full-YYYY-MM-DD.service -n 30 --no-pager
```

上述命令只允许发送当日已生成且未发送的完整日报，并核对收件人绑定等条件。若 SMTP 结果不确定，不要自动重发；先核对收件箱、服务日志和邮件服务端投递记录。

## 启用每日 08:00 正式自动发送

正式发送 timer 默认禁用。先完成数据库初始化、凭据检查、真实预览审阅和手工测试邮件，再由管理员明确批准正式每日投递。下方命令执行预检并启用 timer；脚本会要求现场输入 `ENABLE` 二次确认，若预览 timer 已启用则先停用它：

```bash
cd /opt/newsdaily
sudo bash scripts/install_debian13.sh --enable-timer
systemctl list-timers newsdaily-send.timer
```

上述启用命令配置为每日北京时间 08:00 运行 `full-run --send`。若系统停机错过触发时间，`Persistent=true` 会在恢复后补跑。

若一键脚本无法使用，且已完成上文的凭据检查、真实预览审阅和一次测试邮件，可由管理员手工启用正式 timer。**下方命令会开始每日真实投递**；它先关闭旧预览 timer，再启用正式 timer。不要在未确认收件人和测试结果时执行：

```bash
sudo systemctl disable --now newsdaily-preview.timer
sudo systemctl enable --now newsdaily-send.timer
systemctl list-timers newsdaily-send.timer
```

暂停正式自动发送使用下方命令；需要恢复时，重新使用前述带人工确认的 `--enable-timer` 命令，或在重新完成检查后执行上方手工启用步骤：

```bash
sudo systemctl disable --now newsdaily-send.timer
```

下方命令只检查计划时间与 timer 状态，不会启用或触发发送：

```bash
systemd-analyze calendar '*-*-* 08:00:00 Asia/Shanghai'
systemctl is-enabled newsdaily-send.timer || true
systemctl list-timers --all 'newsdaily-*'
```

## 诊断与回滚

下方命令查看预检、单元状态、timer 计划和最近服务日志。日志可能含业务运行信息，分享前检查并脱敏；不要输出凭据文件：

```bash
cd /opt/newsdaily
sudo bash scripts/install_debian13.sh --check
sudo systemctl status newsdaily-send.timer newsdaily-preview.timer newsdaily-send.service --no-pager
sudo journalctl -u newsdaily-send.service -n 100 --no-pager
sudo journalctl -u newsdaily-preview.service -n 100 --no-pager
sudo -u newsdaily .venv/bin/python -B -m app.cli status
```

若只是暂停计划任务，下方命令禁用正式及预览 timer；不会删除数据或凭据：

```bash
sudo systemctl disable --now newsdaily-send.timer newsdaily-preview.timer
```

若最近一次安装替换了旧 systemd 单元，安装脚本会在 `/etc/systemd/system/` 留下带 `.newsdaily-backup-时间戳` 后缀的备份。确认准确备份文件后，可用下方命令恢复单个 unit（把占位符替换为已核实文件名），随后 daemon-reload；不要覆盖其他服务单元：

```bash
sudo install -o root -g root -m 0644 /etc/systemd/system/UNIT.service.news... /etc/systemd/system/UNIT.service
sudo systemctl daemon-reload
```

回滚到不再运行此应用时，先停用其 timer，再备份所需业务数据。下方命令移除 timer 的开机启用状态并停止服务；它不删除数据库、输出、配置、凭据或程序文件：

```bash
sudo systemctl disable --now newsdaily-send.timer newsdaily-preview.timer
sudo systemctl stop newsdaily-send.service newsdaily-preview.service
```

删除应用文件、数据库或凭据属于不可逆维护操作，不包含在常规回滚步骤中。确需卸载时，先确认并另行备份 `/opt/newsdaily/data/`、`output/`、`/etc/newsdaily/config` 和 `/etc/newsdaily/credentials/`，由管理员逐项决定保留或清除；不要用宽泛递归删除命令。
