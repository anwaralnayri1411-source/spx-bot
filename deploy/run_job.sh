#!/bin/bash
# مشغّل مهام البوت على الخادم (بديل GitHub Actions). كل مهمة تطابق سير العمل الذي يحمل اسمها في .github/workflows:
# نفس الشرط ونفس الأوامر ونفس المتغيرات ونفس الملفات المحفوظة. الاستدعاء: run_job.sh <job>  (يشغّله spxbot@<job>.service)
#
# الوضع من SPXBOT_MODE في /etc/spxbot/runner.env:
#   shadow (الافتراضي): نسخة منفصلة تبدأ من آخر حالة في GitHub، بلا مفاتيح تيليجرام، ولا رفع (الدفع محجوب بثلاث طرق).
#                       ما كان سيُرسل يُطبع في /var/log/spxbot/<job>.log للمقارنة مع GitHub.
#   live: إرسال حقيقي وحفظ السجلات في المستودع (git push بمفتاح النشر).
# كل الجسم داخل main حتى يقرأ bash الملف كاملاً قبل التنفيذ (git pull قد يغيّره أثناء تشغيل مهمة طويلة).
set -uo pipefail

main() {
  local job="${1:?job name}"
  local mode="${SPXBOT_MODE:-shadow}"
  local base=/opt/spxbot py=/opt/spxbot/venv/bin/python
  local logdir=/var/log/spxbot
  mkdir -p "$logdir"

  # ---- تعريف المهام (من ملفات سير العمل) ----
  local group="" files="" msg="" envs="" long=0 gate="" extra_env=""
  local -a cmds=() after=()
  case "$job" in
    bot)
      group=spx-bot; envs="TG ALPACA LQ TASTY"; extra_env="FORCE=0"
      cmds=("$py options_bot.py")
      files="zero_dte_log.csv messages_options_bot.jsonl bot_state.json spx_positions.json spx_track.csv live_quotes_log.csv signals_extra_log.csv levels_log.csv mgt_compare_log.csv news_shadow_log.csv edge7_shadow_state.json edge7_shadow_log.csv data_gaps_spx_log.csv blocked_inbox_spx.csv"
      msg="update bot state" ;;
    spx_monitor)
      group=spx-bot; envs="TG ALPACA LQ TASTY"; gate=gate_open_positions
      cmds=("$py spx_monitor.py")
      files="spx_positions.json spx_track.csv spx_thesis_log.csv messages_options_bot.jsonl live_quotes_log.csv"
      msg="update spx monitor" ;;
    stocks)
      group=stocks-bot; envs="TG ALPACA LQS TASTY"; extra_env="FORCE=0 BOT_MODE=scan"
      cmds=("$py stocks_bot.py")
      files="stocks_log.csv stocks_track.csv messages_stocks_bot.jsonl stocks_state.json stocks_positions.json stocks_shadow_log.csv stocks_flow_log.csv live_quotes_stocks_log.csv stocks_sector_live_log.csv data_gaps_stocks_log.csv blocked_inbox_stocks.csv"
      msg="update stocks state" ;;
    commands)
      group=commands-bot; envs="TG ALPACA LQS TASTY"
      cmds=("$py commands_bot.py")
      files="commands_state.json messages_commands_bot.jsonl stocks_mine.json"
      msg="update commands state" ;;
    preopen)
      group=preopen-bot; envs="TG"; extra_env="FORCE=0 RESEND=0"
      cmds=("$py preopen_bot.py")
      files="messages_preopen_bot.jsonl levels_log.csv mgt_compare_log.csv"
      msg="update preopen messages" ;;
    contract_shadow)
      group=contract-shadow; gate="gate_signal_today contract_shadow_state.json"
      cmds=("$py contract_shadow.py" "$py contract_shadow.py --report")
      files="contract_shadow_state.json contract_shadow_track.csv contract_shadow_log.csv research/contract_shadow_results.md"
      msg="contract shadow log" ;;
    live_shadow)
      group=live-shadow; envs="ALPACA"; gate="gate_signal_today live_shadow_state.json"
      cmds=("$py research/live_shadow.py" "$py research/live_shadow.py --report")
      files="live_shadow_state.json live_shadow_track.csv live_shadow_log.csv research/live_shadow_results.md"
      msg="live shadow log" ;;
    feed_compare)
      group=feed-compare; envs="ALPACA"; gate="gate_until 2026-10-23"
      cmds=("$py research/feed_compare.py" "$py research/feed_compare.py --report")
      files="research/feed_compare_obs.csv research/feed_compare_results.md"
      msg="feed compare log" ;;
    gex)
      group=gex
      if [ "$(date -u +%H)" = "13" ]; then cmds=("$py gex_shadow.py --snap"); else cmds=("$py gex_shadow.py --eval"); fi
      files="gex_snapshots.csv gex_outcomes.csv"
      msg="gex shadow log" ;;
    shadow)
      group=shadow
      cmds=("$py shadow_levels.py --daily" "$py ladder_shadow.py --daily" "$py shadow_confluence.py --daily" "$py shadow_ob_score.py --daily")
      files="shadow_grid_log.csv shadow_grid_placebo_log.csv shadow_ob_log.csv shadow_fvg_log.csv shadow_days.txt ladder_shadow_log.csv ladder_days.txt shadow_confluence_log.csv shadow_confluence_days.txt shadow_ob_score_log.csv shadow_ob_score_days.txt shadow_ob_plan_log.csv"
      msg="shadow levels log" ;;
    weekly)
      group=weekly; envs="TG"
      cmds=("$py weekly.py")
      files="messages_*.jsonl"
      msg="weekly report log" ;;
    news_shadow)
      group=news-shadow
      cmds=("$py research/news_shadow_eval.py")
      files="news_shadow_outcomes.csv research/news_shadow_results.md"
      msg="news shadow results" ;;
    ladder_exit)
      group=ladder-exit
      cmds=("$py research/ladder_exit_shadow.py")
      files="ladder_exit_log.csv research/ladder_exit_results.md"
      msg="ladder exit shadow results" ;;
    stocks_sector)
      group=stocks-sector
      cmds=("$py research/stocks_sector_study.py" "$py research/stocks_open_stop_study.py")
      files="stocks_sector_log.csv research/stocks_sector_results.md research/stocks_open_stop_results.md"
      msg="stocks sector study results" ;;
    spx_cache)
      group=spx-cache
      cmds=("$py research/spx_cache_update.py")
      files="research/spx_5m_cache.csv"
      msg="spx 5m cache update" ;;
    tail_log)
      group=tail-log; envs="ALPACA"
      cmds=("$py research/tail_log.py")
      files="tail_log_days.csv tail_log_events.csv"
      msg="tail log" ;;
    exit_shadow)
      group=exit-shadow; envs="ALPACA TASTY"; gate="gate_signal_today exit_shadow_state.json"
      cmds=("$py exit_shadow.py" "$py exit_shadow.py --report")
      files="exit_shadow_state.json exit_shadow_log.csv research/exit_shadow_results.md data_gaps_exit_shadow_log.csv"
      msg="exit shadow log" ;;
    blocked_shadow)
      group=blocked-shadow; envs="ALPACA TASTY"
      cmds=("$py blocked_shadow.py" "$py blocked_shadow.py --report")
      files="blocked_shadow_state.json blocked_shadow_log.csv research/blocked_shadow_results.md data_gaps_blocked_shadow_log.csv"
      msg="blocked shadow log" ;;
    stocks_exit_study)
      group=stocks-exit-study
      cmds=("$py research/stocks_exit_study.py")
      files="research/stocks_exit_study_results.md"
      msg="stocks exit study refresh" ;;
    real_price_check)
      group=real-price-check; envs="ALPACA"
      cmds=("$py research/real_price_check.py")
      files="research/real_price_check_results.md research/real_price_check_trades.csv"
      msg="real price check refresh" ;;
    weekly_learning)
      group=weekly-learning; envs="TG"
      cmds=("$py research/weekly_learning.py")
      files="research/weekly_learning.md"
      msg="weekly learning summary" ;;
    stocks_watcher)
      group=stocks-watcher; envs="TG ALPACA ANTHROPIC LQS TASTY"; long=1
      cmds=("$py stocks_watcher.py")
      files="stocks_watcher_state.json stocks_watcher_log.csv messages_stocks_watcher.jsonl stocks_watcher_health.json"
      msg="stocks watcher log" ;;
    momentum_shadow)
      group=momentum-shadow; envs="ALPACA"; long=1
      cmds=("$py research/momentum_shadow.py --loop"); after=("$py research/momentum_shadow.py --report")
      files="momentum_shadow_state.json momentum_shadow_log.csv research/momentum_shadow_results.md"
      msg="momentum shadow log" ;;
    preopen_shadow)
      group=preopen-shadow; envs="ALPACA TASTY"
      cmds=("$py research/preopen_shadow.py")
      files="preopen_shadow_state.json preopen_shadow_log.csv preopen_shadow_msgs.jsonl research/preopen_shadow_results.md"
      msg="preopen shadow log" ;;
    market_read)
      group=market-read; envs="TG"; gate="gate_ny_times 1005 1300"
      cmds=("$py market_read.py --all --session-only")
      msg="market read" ;;
    market_live)
      group=market-live; envs="TASTY"; long=1
      cmds=("$py market_live.py --loop")
      msg="market live" ;;
    tasty_health)
      group=tasty-health; envs="TG TASTY"
      cmds=("$py tasty_health.py")
      files="tasty_health.json"
      msg="tasty health" ;;
    news_memory)
      group=news-memory; envs="ANTHROPIC"
      cmds=("$py research/news_memory.py --collect")
      files="news_memory_log.csv news_memory_state.json"
      msg="news memory log" ;;
    news_memory_eval)
      group=news-memory
      cmds=("$py research/news_memory.py --eval")
      files="news_memory_outcomes.csv research/news_memory_results.md"
      msg="news memory outcomes" ;;
    live_watcher)
      group=live-watcher; envs="ALPACA"; long=1
      cmds=("$py research/live_watcher.py"); after=("$py research/live_watcher.py --report")
      files="live_watcher_state.json live_watcher_signals.csv live_watcher_edges.csv live_watcher_runs.csv live_watcher_health.json research/live_watcher_results.md"
      msg="live watcher log" ;;
    *) echo "unknown job: $job" >&2; return 2 ;;
  esac

  # بوت الأوامر يسحب ضغطات تيليجرام (getUpdates)؛ في الظل كان سيسرقها من نسخة GitHub، فلا يعمل.
  if [ "$mode" = shadow ] && [ "$job" = commands ]; then echo "shadow: commands skipped"; return 0; fi

  # المهام الطويلة: الكود ينتظر حتى 9:31 وينهي عند 16:00 نيويورك أو بعد 345 دقيقة، والمؤقت يعيد تشغيلها؛
  # بعد الإغلاق لا داعي لتشغيلها (كانت ستكتب نبضة فقط).
  if [ "$long" = 1 ]; then
    local hm; hm=$(TZ=America/New_York date +%H%M)
    if [ "$hm" -ge 1600 ] || [ "$hm" -lt 700 ]; then return 0; fi
  fi

  exec 9>"/run/lock/spxbot-$group.lock"
  flock -w 1800 9 || { echo "lock timeout ($group)"; return 1; }

  local log="$logdir/$job.log" t0; t0=$(date -u +%FT%TZ)
  {
    echo "=== $t0 start $job mode=$mode"
    prepare_tree "$mode" "$group" || { echo "=== git prepare failed"; return 1; }
    cd "$base/$mode/$group" || return 1

    if [ -n "$gate" ] && ! $gate; then echo "=== gate: skip"; echo "=== $(date -u +%FT%TZ) end rc=0 (gated)"; return 0; fi

    local rc=0 c
    for c in "${cmds[@]}"; do
      run_env "$mode" "$envs" "$extra_env" "$long" $c || { rc=$?; break; }
    done
    if [ "$long" = 1 ]; then              # خطوة الحفظ في سير العمل: if: always()
      for c in "${after[@]}"; do run_env "$mode" "$envs" "$extra_env" "$long" $c || true; done
    fi
    if [ "$rc" = 0 ] || [ "$long" = 1 ]; then save_files "$mode" "$msg" $files; fi
    git status --short | sed 's/^/    /'
    echo "=== $(date -u +%FT%TZ) end rc=$rc"
    echo "$t0,$(date -u +%FT%TZ),$job,$mode,$rc" >> "$logdir/runs.csv"
    return $rc
  } >> "$log" 2>&1
}

# نسخة لكل مجموعة concurrency (مثل checkout مستقل في GitHub)، مُعادة إلى origin/main قبل كل تشغيل.
prepare_tree() {
  local mode=$1 group=$2 dir=/opt/spxbot/$1/$2
  if [ ! -d "$dir/.git" ]; then
    mkdir -p "$(dirname "$dir")"
    if [ "$mode" = live ]; then
      git clone -q git@github.com:anwaralnayri1411-source/spx-bot.git "$dir" || return 1
    else
      git clone -q https://github.com/anwaralnayri1411-source/spx-bot.git "$dir" || return 1
      printf '#!/bin/sh\necho "shadow: push blocked" >&2\nexit 1\n' > "$dir/.git/hooks/pre-push"
      chmod +x "$dir/.git/hooks/pre-push"
    fi
  fi
  git -C "$dir" fetch -q origin main || return 1
  git -C "$dir" rebase --abort 2>/dev/null
  git -C "$dir" stash clear 2>/dev/null
  git -C "$dir" reset -q --hard && git -C "$dir" clean -fdq &&
    git -C "$dir" checkout -q -B main origin/main && git -C "$dir" reset -q --hard origin/main
}

run_env() {
  local mode=$1 envs=$2 extra=$3 long=$4; shift 4
  local -a e=(HOME="$HOME" PATH=/opt/spxbot/venv/bin:/usr/bin:/bin LANG=C.UTF-8 PYTHONUNBUFFERED=1)
  local k
  for k in $envs; do
    case $k in
      TG) [ "$mode" = live ] && e+=(TELEGRAM_TOKEN="${TELEGRAM_TOKEN:-}" TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}" TELEGRAM_EXTRA_CHAT_IDS="${TELEGRAM_EXTRA_CHAT_IDS:-}") ;;
      ALPACA) e+=(ALPACA_KEY="${ALPACA_KEY:-}" ALPACA_SECRET="${ALPACA_SECRET:-}") ;;
      ANTHROPIC) e+=(ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}") ;;
      TASTY) e+=(TASTY_CLIENT_SECRET="${TASTY_CLIENT_SECRET:-}" TASTY_REFRESH_TOKEN="${TASTY_REFRESH_TOKEN:-}") ;;
      LQ) e+=(LIVE_QUOTES_SPX="${LIVE_QUOTES_SPX:-shadow}") ;;
      LQS) e+=(LIVE_QUOTES_STOCKS="${LIVE_QUOTES_STOCKS:-shadow}") ;;
    esac
  done
  [ -n "$extra" ] && e+=($extra)
  # المهام الطويلة تقرأ ملفات البوت من origin وتحفظ منتصف الجلسة حين GITHUB_ACTIONS=true؛ في الظل الدفع محجوب.
  [ "$long" = 1 ] && e+=(GITHUB_ACTIONS=true)
  [ "$mode" = shadow ] && e+=(GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND=false)
  echo "--- $(date -u +%T) $*"
  env -i "${e[@]}" "$@"
}

gate_open_positions() {
  python3 -c "import json;d=json.load(open('spx_positions.json'));print('true' if any(p.get('status') in ('open','tp') for p in d.get('positions',[])) else 'false')" 2>/dev/null | grep -q true
}

gate_signal_today() {
  python3 - "$1" <<'EOF' 2>/dev/null | grep -q true
import json, sys, datetime, zoneinfo
today = datetime.datetime.now(zoneinfo.ZoneInfo("America/New_York")).date().isoformat()
try:
    pos = json.load(open("spx_positions.json")).get("positions", [])
except Exception:
    pos = []
try:
    st = json.load(open(sys.argv[1]))
except Exception:
    st = {}
sig = any(p.get("sym") in ("SPX", "SPY") and p.get("exp") == today for p in pos)
print("true" if sig or st.get("items") else "false")
EOF
}

# يمر فقط إن كان وقت نيويورك ضمن 10 دقائق بعد أحد الأوقات (HHMM)؛ المؤقت يضع موعدين لكل وقت (صيفي/شتوي).
gate_ny_times() {
  local hm t; hm=$((10#$(TZ=America/New_York date +%H%M)))
  for t in "$@"; do t=$((10#$t)); [ "$hm" -ge "$t" ] && [ "$hm" -lt $((t + 10)) ] && return 0; done
  return 1
}

gate_until() { [[ ! "$(TZ=America/New_York date +%F)" > "$1" ]]; }

save_files() {
  local mode=$1 msg=$2 f; shift 2
  for f in $@; do git add "$f" 2>/dev/null || true; done
  git diff --cached --quiet && return 0
  if [ "$mode" != live ]; then echo "=== shadow: would commit \"$msg\": $(git diff --cached --name-only | tr '\n' ' ')"; return 0; fi
  git -c user.name=spx-bot-server -c user.email=bot@users.noreply.github.com commit -q -m "$msg" || return 0
  local i
  for i in 1 2 3 4 5; do
    # --autostash: ملف متتبَّع عدّله الكود ولا تحفظه هذه المهمة (مثل data_gaps_stocks_log.csv في مراقب الشركات)
    # كان يمنع pull --rebase فيضيع حفظ التشغيل كله؛ يبقى ذلك الملف خارج الالتزام والتشغيل التالي يبدأ نظيفاً
    if git pull -q --rebase --autostash origin main; then git push -q origin HEAD:main && return 0; else git rebase --abort 2>/dev/null; fi
    sleep 5
  done
  echo "=== push failed"
  return 1
}

main "$@"
exit $?
