# SMS Module — MSG91 Transactional Endpoints

All endpoints are public (no auth required). On MSG91 failure, returns HTTP 503.

Base URL: `/sms`

---

## Endpoints

### POST `/sms/send-welcome`
Sent when a user signs up for the first time.

```json
{
  "country_code": "+91",
  "mobile": "9876543210",
  "name": "Riya"
}
```

---

### POST `/sms/send-report-ready`
Sent when the lab report is ready for the user to view.

```json
{
  "country_code": "+91",
  "mobile": "9876543210",
  "url": "https://nucleotide.life/reports/abc123"
}
```

---

### POST `/sms/send-order-confirmation`
Sent when an order is confirmed and the collection partner will follow up.

```json
{
  "country_code": "+91",
  "mobile": "9876543210",
  "order_id": "ORD-20240501-001"
}
```

---

### POST `/sms/send-phlebo-blood`
Sent for blood test orders — Thyrocare (phlebotomist) will call to schedule collection.

```json
{
  "country_code": "+91",
  "mobile": "9876543210",
  "order_id": "ORD-20240501-001"
}
```

---

### POST `/sms/send-phlebo-genetic`
Sent for genetic test orders — MedGenome will call within 24–48 hrs for sample collection.

```json
{
  "country_code": "+91",
  "mobile": "9876543210",
  "order_id": "ORD-20240501-001"
}
```

---

### POST `/sms/send-post-order-survey`
Sent after order completion — prompts the user to fill a 5-minute personalisation survey.

```json
{
  "country_code": "+91",
  "mobile": "9876543210",
  "survey_url": "https://forms.nucleotide.life/survey/xyz"
}
```

---

## Response

**200 OK**
```json
{ "status": "success", "message": "SMS sent successfully." }
```

**503 Service Unavailable**
```json
{ "detail": "SMS service temporarily unavailable." }
```

---

## Template IDs

Configured via environment variables (`.env` locally, container env vars on AWS App Runner):

| Env Var | Template |
|---|---|
| `MSG91_TEMPLATE_ID_WELCOME_FIRST_TIME` | welcome_first_time |
| `MSG91_TEMPLATE_ID_REPORT_READY` | report_ready |
| `MSG91_TEMPLATE_ID_ORDER_CONFIRMATION` | order_confirmation |
| `MSG91_TEMPLATE_ID_PHLEBO_COLLECTION_BLOOD` | phlebo_collection_blood |
| `MSG91_TEMPLATE_ID_PHLEBO_COLLECTION_GENETIC` | phlebo_collection_genetic |
| `MSG91_TEMPLATE_ID_POST_ORDER_SURVEY` | post_order_survey |
