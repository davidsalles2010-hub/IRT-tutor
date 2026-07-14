# Human review required before real use

Everything in this file must be resolved before MathLens is used with real (let alone
paying) students. The statistical engine is tested; the *content* is not.

## 1. Question bank (`data/questions.json`) — highest priority

Authored by an AI assistant. A math educator should verify, item by item:

- [ ] The keyed answer is correct (all 72 items).
- [ ] Wording is unambiguous and age-appropriate; notation renders correctly on all devices.
- [ ] Distractors are plausible but definitely wrong (no arguably-correct distractors).
- [ ] Topic tags are right (they drive the report's topic breakdown).
- [ ] No item rewards test-taking tricks over math (e.g. guessable from answer formats).

## 2. Difficulty ratings — replace estimates with calibration

The 1–10 difficulty per item is an authored guess, mapped linearly to logits in
`app/irt.py` (`difficulty_to_logit`). Plan:

- [ ] Pilot the bank on a few hundred students (fixed forms, not adaptive).
- [ ] Fit a Rasch model to the pilot responses (e.g. `girth`, `py-irt`, or R's `TAM`/`eRm`)
      and replace authored difficulties with fitted values (store logits directly).
- [ ] Check item fit statistics; drop or rewrite misfitting items.
- [ ] Re-run `python -m scripts.simulate` after calibration.

## 3. Coverage gaps in the bank

- [ ] Thin at the extremes: only 3 items at difficulty 1 and 4 at difficulty 9–10.
      Very weak and very strong students are measured with less precision (the CAT
      runs out of well-targeted items). Add ~6–10 items at each tail.
- [ ] One bank-wide review of Unicode math rendering (superscripts, fraction glyphs)
      on older devices.

## 4. Model limitations (documented, acceptable for preview)

- [ ] Guessing: 4-choice items have a ~25% success floor the Rasch model ignores.
      Consider a 3PL model or Rasch with a guessing correction once calibrated data exists.
- [ ] Topic verdict thresholds in `app/report.py` (`FOCUS_GAP`, `STRENGTH_GAP`,
      `TOPIC_PRIOR_SD`) are sensible defaults, not validated cutoffs — revisit with data.
- [ ] Stopping rule (`SE_STOP = 0.48`, 15–20 questions) — revisit after calibration.

## 5. Engineering before scale

- [ ] Sessions are in process memory (`app/adaptive.py: SessionStore`). Fine for one
      free-tier instance; move to Redis/Postgres before multiple workers or restarts
      that must preserve sessions.
- [ ] Add rate limiting if the API is ever exposed beyond the frontend.
- [ ] No PII is collected by design; keep it that way or add a privacy policy first.
