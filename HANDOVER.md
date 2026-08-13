# Harness Methodology — Session Handover

**Checkpoint**: `P6-gate4-20260813`  
**Phase**: P6 — Full Review / Gate 4  
**Generated**: 2026-08-13T06:29:20Z

> ⚠️  **開始下一個工作階段前，請先執行 `/compact` 壓縮上下文**，再從「接下來的工作」繼續。

---

## ▶ 立即開始（兩步）

```bash
# 1. Clone (if working directory cleared)
git clone --recurse-submodules https://github.com/johnnylugm-tech/taskq-api && cd taskq-api

# 2. Read plan and start Phase 7
cat .methodology/phase7_plan.md
# Follow SKILL.md §0.1 Phase 7 entry check, then execute
```

---

## 快速接手指令（詳細）

```bash
# Clone (--recurse-submodules required for harness submodule)
git clone --recurse-submodules https://github.com/johnnylugm-tech/taskq-api /tmp/taskq-api && cd /tmp/taskq-api

# Confirm latest commits
git log --oneline -3

# Confirm FSM state
cat .methodology/state.json   # expected: phase=6 state=RUNNING last_gate=4

# Read active plan
cat .methodology/phase7_plan.md
```

| 欄位 | 值 |
|------|----|
| Remote | `https://github.com/johnnylugm-tech/taskq-api` |
| Branch | `main` |
| State | `phase=6 state=RUNNING last_gate=4` |
| Plan | `.methodology/phase7_plan.md` |

---

## 任務背景

Gate 4 PASS — quality cycle complete.

## 目前執行狀況

Gate 4 PASS: score=95.3. — pipeline complete

## 接下來的工作

1. Proceed to P7: Risk Register
2. Document all known risks
3. On P7 done → call commit_and_push_p7()
4. On P8 done → call commit_and_push_p8()

## 注意事項

- 100% follow SKILL.md
- Do NOT commit `.sessi-work/` or `.methodology/` runtime artifacts
- Git failures are warnings — they never block the pipeline

## 附加資訊

- **gate**: 4
- **score**: 95.3

---
*由 `HandoverGenerator` 自動生成。下次 push 時此檔案將被覆寫。*
