"""Budget model for the board: 1,000 students, seven 8-minute mock interviews a month each.

Every unit price below is one of two things. Either it is what THIS account
(445363794125) was billed per unit on 2026-09-16, read from Cost Explorer with
RECORD_TYPE=Usage so the promotional credits are excluded, or it is the AWS price
list for ap-south-1 / ap-northeast-1 for an item the deployment does not buy yet
(marked "list"). Re-run it after changing an assumption;
docs/budget-1000-students-2026-09.md quotes its output verbatim.

    python tools/cost/budget_1000_students.py

Standard library only. Nothing here talks to AWS.
"""

FX = 96.0          # INR per USD, 2026-09-18 (95.96 on open.er-api.com)
GST = 0.18         # charged on the net invoice
HOURS = 730        # billing hours in a month

# --- unit prices measured on this account, 2026-09-16 --------------------
FARGATE_VCPU_H, FARGATE_GB_H = 0.02383, 0.00261      # Fargate ARM64, Mumbai
TASK_H = 0.5 * FARGATE_VCPU_H + 1.0 * FARGATE_GB_H  # one 0.5 vCPU / 1 GB api task
NAT_H, NAT_GB = 0.056, 0.056                         # NAT gateway hour, GB processed
RDS_MICRO_MAZ_H = 0.042                              # db.t4g.micro Multi-AZ (running today)
RDS_SMALL_MAZ_H = 0.084                              # db.t4g.small Multi-AZ (list)
RDS_GP3_MAZ_GB_MO, RDS_BACKUP_GB_MO = 0.262, 0.095
ALB_H, LCU_H = 0.0239, 0.008
IPV4_H = 0.005
INTER_REGION_GB = 0.086                              # Mumbai -> Tokyo / Singapore
CF_TO_ORIGIN_GB = 0.16                               # CloudFront, India, viewer -> origin
CF_DTO_GB, CF_FREE_TB = 0.109, 1.0                   # CloudFront, India, after the free 1 TB (list)
CW_LOGS_GB = 0.67                                    # CloudWatch Logs ingestion, Mumbai (list)
EFS_STD_GB_MO, EFS_IA_GB_MO, EFS_IA_WRITE_GB = 0.33, 0.0272, 0.011   # list
S3_STD_GB_MO = 0.025                                                 # list
BACKUP_EFS_MUMBAI_GB_MO, BACKUP_EFS_SG_GB_MO, BACKUP_XREGION_GB = 0.055, 0.06, 0.106  # list
# Amazon Nova 2 Sonic in Tokyo, USD per million tokens, as billed
NOVA_IN, NOVA_OUT, NOVA_TIN, NOVA_TOUT = 3.63, 14.52, 0.396, 3.311
NOVA_US_IN, NOVA_US_OUT = 3.00, 12.00               # US-region list price, for the option below
# Amazon Nova Pro (APAC inference profile, Mumbai), as billed
PRO_IN, PRO_OUT = 0.94, 3.76

# --- today's fixed platform, USD per day, from the 2026-09-16 usage records --
TODAY = {
    "NAT gateway hours": 1.344,
    "NAT data processing (8 GB/day today)": 0.446,
    "RDS db.t4g.micro Multi-AZ": 1.008,
    "Fargate 2 x (0.5 vCPU, 1 GB) ARM64": 0.707,
    "ALB hours + LCU": 0.575,
    "CloudWatch metrics + alarms": 0.393,
    "Public IPv4 x 3": 0.36,
    "RDS storage gp3 20 GB Multi-AZ": 0.175,
    "WAF (ACL + 3 rules + requests)": 0.270,
    "RDS backups (Mumbai + Singapore)": 0.144,
    "Backup copy transfer to Singapore": 0.211,
    "Regional data transfer": 0.069,
    "Secrets Manager": 0.028,
    "S3 + ECR + EFS + SES + misc": 0.06,
    "AWS Backup vault storage (lumpy, ~$3.6/mo)": 0.12,
}
today_month = sum(TODAY.values()) * 30.4

# --- the load and the interview ---------------------------------------------
STUDENTS, PER_DAY, MINUTES = 1000, 2, 8.0   # PER_DAY = the ceiling the board first asked about
PER_STUDENT_PER_MONTH = 7                     # the volume the board is planning for (2026-09-18)
HEADLINE = STUDENTS * PER_STUDENT_PER_MONTH  # 7,000 interviews a month
SECONDS = MINUTES * 60
TOKENS_PER_S = 25                       # AWS: Nova Sonic audio is 25 tokens per second
# Talk-time shares of an 8-minute session, and text tokens per SESSION.
# Measured on this deployment (API log + one-minute Bedrock metrics, 17 Sep):
# the one session that ran to the 8-minute cap streamed 343 s of microphone
# audio and was billed 1,509 input speech tokens (60 s of speech), 3,252 output
# speech tokens (130 s, 27% of the session), 2,979 text-input and 1,025
# text-output tokens -- $0.057. So silence is not billed, and the prompt is
# counted once per session, not once per turn. The tester barely spoke; a real
# student answers at length, which is what the cases below vary.
CASES = {
    "low":      dict(student=0.35, interviewer=0.25, text_in=3000, text_out=1200),
    "central":  dict(student=0.50, interviewer=0.30, text_in=4000, text_out=1600),
    "high":     dict(student=0.60, interviewer=0.40, text_in=5000, text_out=2000),
}
MEASURED_CAPPED_SESSION = dict(speech_in=1509, speech_out=3252, text_in=2979, text_out=1025)
AGENT_ASKS_PER_STUDENT_MONTH = 10       # REEP Agent questions (Nova Pro), an assumption
LOG_MB_PER_INTERVIEW = 0.5              # CloudWatch Logs written per interview, an assumption


def nova_per_interview(case="central", price_in=NOVA_IN, price_out=NOVA_OUT, seconds=None):
    c = CASES[case]
    s = seconds or SECONDS
    tin = s * TOKENS_PER_S * c["student"]
    tout = s * TOKENS_PER_S * c["interviewer"]
    text_in = c["text_in"]
    text_out = c["text_out"]
    return dict(speech_in=tin * price_in / 1e6, speech_out=tout * price_out / 1e6,
                text_in=text_in * NOVA_TIN / 1e6, text_out=text_out * NOVA_TOUT / 1e6,
                tokens=(int(tin), int(tout), int(text_in), int(text_out)))


def network_per_interview(case="central", seconds=None):
    s = seconds or SECONDS
    interviewer = CASES[case]["interviewer"]
    up_client = 48_000 * s / 1e9                    # browser mic, 24 kHz PCM16, GB
    down_client = 48_000 * s * interviewer / 1e9
    up_tokyo = 32_000 * 4 / 3 * s / 1e9             # 16 kHz PCM16 as base64 JSON events
    down_tokyo = 48_000 * 4 / 3 * s * interviewer / 1e9
    return {
        "CloudFront: mic audio to origin ($0.16/GB India)": up_client * CF_TO_ORIGIN_GB,
        "NAT processing, both directions to Tokyo": (up_tokyo + down_tokyo) * NAT_GB,
        "Inter-region transfer Mumbai -> Tokyo": up_tokyo * INTER_REGION_GB,
        "ALB LCU (processed bytes)": (up_client + down_client) * LCU_H,
        f"CloudWatch Logs ({LOG_MB_PER_INTERVIEW} MB per interview, assumed)": LOG_MB_PER_INTERVIEW / 1000 * CW_LOGS_GB,
    }


def per_interview(case="central", price_in=NOVA_IN, price_out=NOVA_OUT, seconds=None):
    nova = nova_per_interview(case, price_in, price_out, seconds)
    nova_each = sum(v for k, v in nova.items() if k != "tokens")
    net_each = sum(network_per_interview(case, seconds).values())
    return nova_each, net_each


def scenario(interviews_per_month, days_per_month=22, case="central", recording=False,
             price_in=NOVA_IN, price_out=NOVA_OUT, minutes=None):
    s = (minutes or MINUTES) * 60
    n = interviews_per_month
    nova_each, net_each = per_interview(case, price_in, price_out, s)
    lines = {}
    lines["Today's platform (measured, list price)"] = today_month
    lines["Database step-up to db.t4g.small Multi-AZ"] = (RDS_SMALL_MAZ_H - RDS_MICRO_MAZ_H) * HOURS
    # Extra api task-hours scale with the interviews: 3 tasks x 12 h x 22 days at
    # 44,000 a month is 0.018 task-hours per interview; at 7,000 a month the two
    # always-on tasks carry the load and this rounds to nothing.
    lines["API scale-out during interview hours (0.018 task-hours per interview)"] = n * 0.018 * TASK_H
    lines["Database storage growth (+1.2 GB/month, incl. backups)"] = 5.0
    lines["Nova 2 Sonic interviews"] = n * nova_each
    down_gb_month = n * 48_000 * s * CASES[case]["interviewer"] / 1e9
    cf_dto = max(0.0, down_gb_month - CF_FREE_TB * 1000) * CF_DTO_GB
    lines["Network and logs for interviews"] = n * net_each + cf_dto
    lines[f"REEP Agent (Nova Pro, {AGENT_ASKS_PER_STUDENT_MONTH} questions/student/month)"] = (
        STUDENTS * AGENT_ASKS_PER_STUDENT_MONTH * (3000 * PRO_IN + 300 * PRO_OUT) / 1e6)
    lines["Mail (SES, ~10 messages/student/month)"] = STUDENTS * 10 * 0.00016
    if recording:
        tb_month = n * 46.08e6 / 1e12          # two tracks x 24 kHz x 16-bit x 480 s per interview
        tb_live = tb_month * 6                 # 180-day retention
        lines["Recording: EFS Standard (first 30 days)"] = tb_month * 1000 * EFS_STD_GB_MO
        lines["Recording: EFS Infrequent Access (days 31-180)"] = (
            tb_month * 5 * 1000 * EFS_IA_GB_MO + tb_month * 1000 * EFS_IA_WRITE_GB)
        lines["Recording: AWS Backup of EFS, Mumbai + Singapore copy"] = (
            tb_live * 1000 * (BACKUP_EFS_MUMBAI_GB_MO + BACKUP_EFS_SG_GB_MO) + tb_month * 1000 * BACKUP_XREGION_GB)
        lines["Recording: S3 documents archive (never deleted; year-1 average)"] = tb_month * 6.5 * 1000 * S3_STD_GB_MO
    total = sum(lines.values())
    return dict(n=n, lines=lines, total=total, gst=total * GST,
                inr_month=total * (1 + GST) * FX, inr_year=total * (1 + GST) * FX * 12)


def show(title, s):
    print(f"\n=== {title}: {s['n']:,} interviews/month ===")
    for k, v in s["lines"].items():
        print(f"  {k:<68} ${v:>9,.0f}")
    print(f"  {'TOTAL before GST':<68} ${s['total']:>9,.0f}")
    print(f"  {'+18% GST':<68} ${s['gst']:>9,.0f}")
    print(f"  per month INR {s['inr_month'] / 1e5:,.2f} lakh | per year INR {s['inr_year'] / 1e7:,.2f} crore"
          f" | per student/month INR {s['inr_month'] / STUDENTS:,.0f}")


if __name__ == "__main__":
    print(f"Today's platform: ${sum(TODAY.values()):.2f}/day = ${today_month:,.0f}/month at list price;"
          f" one api task-hour ${TASK_H:.4f}")
    print("\nPer 8-minute interview, Tokyo prices (USD | INR):")
    for case in ("low", "central", "high"):
        nova = nova_per_interview(case)
        nova_each = sum(v for k, v in nova.items() if k != "tokens")
        net_each = sum(network_per_interview(case).values())
        c = CASES[case]
        print(f"  {case:<8} student {c['student']:.0%}, interviewer {c['interviewer']:.0%},"
              f" text {c['text_in']:,}/{c['text_out']:,} tokens")
        print(f"           speech in ${nova['speech_in']:.4f}  speech out ${nova['speech_out']:.4f}"
              f"  text in ${nova['text_in']:.4f}  text out ${nova['text_out']:.4f}  network ${net_each:.4f}")
        print(f"           tokens (speech in, speech out, text in, text out) = {nova['tokens']}")
        print(f"           TOTAL ${nova_each + net_each:.4f} = INR {(nova_each + net_each) * FX:.1f}"
              f"  (Nova alone ${nova_each:.4f})")
    m = MEASURED_CAPPED_SESSION
    measured = (m["speech_in"] * NOVA_IN + m["speech_out"] * NOVA_OUT + m["text_in"] * NOVA_TIN + m["text_out"] * NOVA_TOUT) / 1e6
    print(f"  measured: the 17 Sep session that ran to the 8-minute cap billed ${measured:.4f} = INR {measured * FX:.1f}"
          f" (tester spoke ~{m['speech_in'] / TOKENS_PER_S:.0f} s, interviewer ~{m['speech_out'] / TOKENS_PER_S:.0f} s)")
    for k, v in network_per_interview("central").items():
        print(f"  {k:<64} ${v:.5f}")
    show("H. THE PLAN: 7 a month per student, central", scenario(HEADLINE, 22))
    show("H-low. 7 a month per student, low case", scenario(HEADLINE, 22, case="low"))
    show("H-high. 7 a month per student, high case", scenario(HEADLINE, 22, case="high"))
    show("H-rec. 7 a month per student, recording ON", scenario(HEADLINE, 22, recording=True))
    show("A. Ceiling: working days (22), 2 a day, central", scenario(44_000, 22))
    show("B. Every day (30), 2 a day, central", scenario(60_000, 30))
    show("A-low. Working days, low case", scenario(44_000, 22, case="low"))
    show("A-high. Working days, high case (both sides talkative)", scenario(44_000, 22, case="high"))
    show("B-low. Every day, low case", scenario(60_000, 30, case="low"))
    show("B-high. Every day, high case", scenario(60_000, 30, case="high"))
    show("C. Every day, recording ON (central)", scenario(60_000, 30, recording=True))
    show("D. Working days, Nova at US-region prices", scenario(44_000, 22, price_in=NOVA_US_IN, price_out=NOVA_US_OUT))
    show("E. Working days, 6-minute interviews", scenario(44_000, 22, minutes=6))
    print("\nScaling table (central case, Tokyo prices):")
    for per_month, what in ((2_000, "pilot: 100 students, 1 a day"), (4_300, "1,000 students, 1 a week"),
                            (HEADLINE, "THE PLAN: 1,000 students, 7 a month"),
                            (8_600, "1,000 students, 2 a week"), (22_000, "1,000 students, 1 a day, working days"),
                            (44_000, "1,000 students, 2 a day, working days"), (60_000, "1,000 students, 2 a day, every day")):
        s = scenario(per_month, 22 if per_month < 60_000 else 30)
        print(f"  {per_month:>7,}/mo  {what:<42} ${s['total']:>8,.0f} before GST = INR {s['inr_month'] / 1e5:>5.2f} lakh/mo incl. GST"
              f"  (${s['total'] * 12 / 1000:,.1f}k/yr)")
    print("\nWhat a monthly budget buys (central case, after the fixed platform and GST):")
    fixed = scenario(0, 22)["total"]
    unit = sum(per_interview("central"))
    for inr in (50_000, 70_000, 100_000, 200_000, 500_000):
        usd = inr / FX / (1 + GST)
        n = max(0.0, (usd - fixed) / unit)
        print(f"  INR {inr / 1e5:>4.1f} lakh/mo -> ${usd:>6,.0f} before GST -> {n:>7,.0f} interviews/mo"
              f" = {n / STUDENTS / 4.3:.2f} per student per week")
    print(f"  (fixed platform + agent + mail = ${fixed:,.0f}/mo; one interview = ${unit:.4f})")
    print("\nConcurrency the quota must cover:")
    per_day = HEADLINE / 22
    for share, hours in ((1.0, 12), (0.6, 3)):
        conc = per_day * share * MINUTES / (hours * 60)
        print(f"  the plan: {per_day:.0f} interviews a working day; {share:.0%} of them inside {hours} h"
              f" -> {conc:.1f} simultaneous on average, 2x burst {2 * conc:.0f}")
    for window_h in (12,):
        avg = STUDENTS * PER_DAY * MINUTES / (window_h * 60)
        print(f"  the ceiling (2 a day): {avg:.0f} simultaneous on average in a {window_h} h window, 3x peak {3 * avg:.0f}")
