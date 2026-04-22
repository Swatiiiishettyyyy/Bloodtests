import json
import math
from datetime import datetime

data = json.load(open('thyrocare_products (1).json', encoding='utf-8'))

NOW = '2026-04-19 00:00:00'

# ──────────────────────────────────────────────────────────────
# CATEGORY MAPPING  (keyword → category)
# ──────────────────────────────────────────────────────────────
CATEGORY_RULES = [
    # order matters – first match wins
    (['HIV', 'HEPATITIS B', 'HEPATITIS C', 'SYPHILIS', 'VDRL', 'STD', 'CHLAMYDIA',
      'HERPES', 'GONORRHEA', 'HCV', 'HBV', 'TPHA', 'RPR'], 'std'),

    (['CANCER', 'TUMOR MARKER', 'CEA', 'AFP', 'PSA', 'CA 125', 'CA-125', 'CA125',
      'CA 19', 'CA19', 'BRCA', 'HER2', 'ONCOLOGY'], 'cancer'),

    (['ALLERGY', 'IgE', 'RAST', 'ALLERGEN'], 'allergy'),

    (['MALARIA', 'DENGUE', 'TYPHOID', 'WIDAL', 'LEPTOSPIRA', 'CHIKUNGUNYA',
      'SCRUB TYPHUS', 'MONSOON', 'FEVER PANEL', 'FEVER PROFILE'], 'monsoon fever'),

    (['THYROID', 'T3', 'T4', 'TSH', 'THYROXINE', 'TRIIODOTHYRONINE',
      'FSH', 'LH', 'PROLACTIN', 'TESTOSTERONE', 'ESTRADIOL', 'PROGESTERONE',
      'CORTISOL', 'INSULIN', 'DHEA', 'AMH', 'HORMONE', 'ANDROGENS',
      'GROWTH HORMONE', 'ACTH', 'PTH'], 'hormones'),

    (['VITAMIN D', 'VITAMIN B12', 'VITAMIN B9', 'FOLATE', 'VITAMIN C',
      'VITAMIN A', 'VITAMIN E', 'VITAMIN K', 'VITAMIN', 'IRON', 'FERRITIN',
      'TRANSFERRIN', 'ZINC', 'MAGNESIUM', 'SELENIUM', 'COPPER',
      'MICRONUTRIENT', 'MINERAL'], 'vitamins'),

    (['CARDIAC', 'HEART', 'TROPONIN', 'CK-MB', 'BNP', 'NT-PROBNP',
      'LIPID', 'CHOLESTEROL', 'HDL', 'LDL', 'TRIGLYCERIDE', 'VLDL',
      'HOMOCYSTEINE', 'CRP HIGH SENSITIVITY', 'HS-CRP', 'LIPOPROTEIN',
      'ATHEROSCLEROSIS', 'APOLIPOPROTEIN'], 'heart'),

    (['LIVER', 'LFT', 'SGPT', 'SGOT', 'ALT', 'AST', 'BILIRUBIN', 'ALK PHOS',
      'ALKALINE PHOSPHATASE', 'GGT', 'ALBUMIN', 'GLOBULIN', 'HEPATIC',
      'LIVER FUNCTION'], 'liver'),

    (['KIDNEY', 'RENAL', 'CREATININE', 'UREA', 'BUN', 'URIC ACID',
      'CYSTATIN', 'GFR', 'ELECTROLYTE', 'SODIUM', 'POTASSIUM', 'CHLORIDE',
      'URINE PROTEIN', 'MICROALBUMIN', 'KFT'], 'kidney'),

    (['BONE', 'CALCIUM', 'PHOSPHORUS', 'OSTEOPOROSIS', 'DEXA',
      'BONE MINERAL', 'COLLAGEN', 'OSTEOCALCIN', 'ALP BONE',
      'PARATHYROID'], 'bone'),

    (['STOOL', 'GUT', 'H. PYLORI', 'HELICOBACTER', 'IBS',
      'INFLAMMATORY BOWEL', 'CALPROTECTIN', 'OCCULT BLOOD',
      'GASTROINTESTINAL', 'GI PANEL', 'FECAL'], 'gut'),

    # Package / full-body checks → demographic categories
    (['WOMEN BASIC', 'WOMEN ADVANCED', 'FEMALE BASIC', 'FEMALE ADVANCED',
      'PCOD', 'PCOS', 'ANTENATAL', 'FERTILITY', 'MENOPAUSE',
      'WOMEN CARE', 'GIRL', 'FEMALE HEALTH'], 'under 25 women'),  # will be refined below

    (['SENIOR CITIZEN', 'ELDERLY', 'SILVER', 'AGE 50', 'GERIATRIC',
      'ABOVE 50'], '50+ men'),

    (['FULL BODY', 'COMPLETE HEALTH', 'AAROGYAM', 'PREVENTIVE',
      'ANNUAL HEALTH', 'MASTER HEALTH', 'COMPREHENSIVE HEALTH',
      'WELLNESS', 'BASIC PROFILE', 'ADVANCED PROFILE',
      'DOCTOR RECOMMENDED', 'HEALTHY PROFILE'], 'popular health packages'),

    # Default fallback → essential tests
]

def get_category(name: str, tests: list) -> str:
    name_upper = name.upper()
    tests_upper = ' '.join(tests).upper()
    combined = name_upper + ' ' + tests_upper

    for keywords, cat in CATEGORY_RULES:
        for kw in keywords:
            if kw.upper() in combined:
                return cat
    return 'essential tests'


# ──────────────────────────────────────────────────────────────
# ABOUT TEXT  (≤50 chars, eye-catching)
# ──────────────────────────────────────────────────────────────
ABOUT_MAP = {
    'HEMOGRAM': '🩸 Complete blood cell count & health check',
    'COMPLETE URINE': '💛 Full urine health & kidney screen',
    'T3-T4': '🦋 Thyroid trio – T3, T4 & TSH',
    'TSH': '🦋 Thyroid function – quick & accurate',
    'LIPID PROFILE': '❤️ Heart risk check – cholesterol & fats',
    'LIVER FUNCTION': '🫁 6-marker liver health deep dive',
    'KIDNEY': '🫘 Kidney function & waste filter check',
    'RENAL': '🫘 Complete kidney health assessment',
    'VITAMIN D': '☀️ Vitamin D – bone & immunity booster',
    'VITAMIN B12': '⚡ B12 levels – energy & nerve health',
    'VITAMIN': '💊 Essential vitamin level check',
    'IRON': '🔴 Iron stores – beat fatigue now',
    'FERRITIN': '🔴 Iron stores – beat fatigue now',
    'THYROID': '🦋 Full thyroid panel – hormone balance',
    'CARDIAC': '❤️ Heart risk markers – stay safe',
    'CHOLESTEROL': '❤️ Cholesterol check – heart health',
    'DIABETES': '🩸 Blood sugar & diabetes risk screen',
    'HBA1C': '🩸 3-month sugar control report card',
    'GLUCOSE': '🩸 Blood sugar – diabetes check',
    'HIV': '🔬 HIV screen – confidential & accurate',
    'HEPATITIS': '🛡️ Hepatitis virus protection check',
    'DENGUE': '🦟 Dengue detection – fever solved fast',
    'MALARIA': '🦟 Malaria parasite rapid detection',
    'TYPHOID': '🌡️ Typhoid fever – quick diagnosis',
    'WIDAL': '🌡️ Typhoid & fever antibody test',
    'CANCER': '🔬 Tumor marker – early cancer screen',
    'PSA': '🔬 Prostate cancer early warning screen',
    'ALLERGY': '🌿 Identify your hidden allergy triggers',
    'TESTOSTERONE': '💪 Testosterone – male hormone check',
    'ESTRADIOL': '🌸 Estrogen level – women\'s health',
    'FSH': '🌸 Fertility hormones – FSH & LH check',
    'PROLACTIN': '🌸 Prolactin – hormone balance check',
    'CORTISOL': '🧠 Stress hormone – cortisol screen',
    'BONE': '🦴 Bone density & calcium health check',
    'CALCIUM': '🦴 Calcium & bone strength panel',
    'STOOL': '🧫 Gut health & digestive analysis',
    'H. PYLORI': '🧫 H. pylori – stomach ulcer check',
    'URIC ACID': '🦵 Uric acid – gout risk check',
    'CRP': '🔥 Inflammation marker – CRP check',
    'FULL BODY': '🏥 Complete body check – peace of mind',
    'AAROGYAM': '🏥 Aarogyam full wellness package',
    'WOMEN': '🌸 Women\'s health – complete care',
    'SENIOR': '🧓 Senior wellness – head to toe',
    'COMPLETE BLOOD': '🩸 Complete blood picture analysis',
    'ELECTROLYTE': '⚡ Body salts & hydration balance',
    'TESTOSTERONE': '💪 Male hormone & vitality check',
    'SEMEN': '🔬 Semen analysis – fertility check',
    'PCOD': '🌸 PCOD/PCOS hormone profile check',
    'PCOS': '🌸 PCOS hormone & metabolic screen',
    'HOMOCYSTEINE': '❤️ Heart risk – homocysteine level',
    'TROPONIN': '❤️ Heart attack marker – troponin',
    'CREATININE': '🫘 Kidney waste filter – creatinine',
    'COMPLETE HEMOGRAM': '🩸 Complete blood count & cell check',
    'PERIPHERAL': '🔬 Blood smear – cell shape analysis',
    'SMOKER': '🚬 Smoker\'s health damage panel',
    'ZINC': '💊 Zinc levels – immunity & wound care',
    'MAGNESIUM': '💊 Magnesium – muscle & nerve check',
    'COPPER': '💊 Copper levels – metabolism check',
    'SELENIUM': '💊 Selenium – antioxidant check',
    'FOLATE': '💊 Folate – cell growth & DNA check',
    'LFT': '🫁 Liver enzymes & function panel',
    'KFT': '🫘 Kidney function test – full panel',
    'HCV': '🛡️ Hepatitis C virus detection test',
    'SYPHILIS': '🔬 Syphilis – STD detection screen',
    'VDRL': '🔬 Syphilis / STD antibody screen',
    'CHIKUNGUNYA': '🦟 Chikungunya fever detection test',
    'SCRUB TYPHUS': '🌡️ Scrub typhus – fever rapid test',
    'AMH': '🌸 Egg reserve – AMH fertility check',
    'PTH': '🦴 Parathyroid – calcium control check',
    'GGT': '🫁 Liver stress marker – GGT check',
    'PROTEIN': '💪 Total protein & albumin check',
}

def get_about(name: str) -> str:
    name_upper = name.upper()
    for keyword, about in ABOUT_MAP.items():
        if keyword in name_upper:
            return about[:50]
    # Generic fallback
    short = name.title()
    fallback = f'🔬 {short} – expert lab test'
    return fallback[:50]


# ──────────────────────────────────────────────────────────────
# WHAT / WHO / WHY  generator
# ──────────────────────────────────────────────────────────────
def get_test_info(name: str, tests: list, category: str):
    name_u = name.upper()
    test_names = ', '.join(tests[:6]) if tests else name

    # ---- WHAT THIS TEST CHECKS ----
    what_map = {
        'HEMOGRAM': f'Measures {min(len(tests),30)} blood parameters including RBC, WBC, platelets, hemoglobin, and differential white cell counts to assess overall blood health.',
        'URINE': 'Analyses urine for glucose, protein, ketones, bacteria, blood cells, and specific gravity to detect infections, kidney issues, and metabolic disorders.',
        'TSH': 'Measures thyroid-stimulating hormone to assess how well the thyroid gland is functioning and detect hypo- or hyperthyroidism.',
        'T3': 'Measures T3, T4, and TSH hormones to give a complete picture of thyroid activity and hormone balance.',
        'THYROID': 'Evaluates thyroid hormone levels (T3, T4, TSH) to detect thyroid disorders affecting metabolism, energy, and mood.',
        'LIPID': 'Measures total cholesterol, HDL, LDL, VLDL, and triglycerides to evaluate cardiovascular disease risk.',
        'LIVER': 'Assesses liver enzymes (SGOT, SGPT, ALP, GGT), bilirubin, proteins, and albumin to evaluate liver health and function.',
        'KIDNEY': 'Measures creatinine, urea, uric acid, and electrolytes to assess kidney filtration and waste removal efficiency.',
        'RENAL': 'Evaluates kidney function markers including creatinine, BUN, electrolytes, and GFR estimation.',
        'VITAMIN D': 'Measures 25-OH Vitamin D levels to detect deficiency affecting bone strength, immunity, and mood.',
        'VITAMIN B12': 'Checks vitamin B12 levels critical for nerve function, red blood cell production, and energy metabolism.',
        'VITAMIN': f'Screens essential vitamin and micronutrient levels including {test_names[:80]} to identify nutritional gaps.',
        'IRON': 'Evaluates iron stores, serum iron, and TIBC to diagnose iron deficiency anemia and iron overload conditions.',
        'FERRITIN': 'Measures ferritin (stored iron), serum iron, and transferrin saturation to assess iron status comprehensively.',
        'HBA1C': 'Measures average blood sugar levels over the past 3 months to monitor diabetes control and long-term glucose management.',
        'GLUCOSE': 'Measures fasting/random blood glucose levels to screen for diabetes and pre-diabetes conditions.',
        'DIABETES': 'Comprehensive diabetes screening including fasting glucose, HbA1c, insulin resistance markers, and associated risk parameters.',
        'CARDIAC': f'Evaluates heart health markers including {test_names[:80]} to assess risk of cardiovascular disease and heart attacks.',
        'CHOLESTEROL': 'Measures lipid fractions including total cholesterol, HDL, LDL, VLDL, and triglycerides for cardiac risk assessment.',
        'HIV': 'Detects HIV-1 and HIV-2 antibodies and/or antigen (p24) in blood to screen for HIV infection.',
        'HEPATITIS B': 'Detects Hepatitis B surface antigen (HBsAg) and related markers to identify active or chronic HBV infection.',
        'HEPATITIS': 'Screens for hepatitis virus markers including antigens and antibodies to detect liver infections.',
        'DENGUE': 'Detects dengue NS1 antigen and IgM/IgG antibodies to diagnose dengue fever quickly and accurately.',
        'MALARIA': 'Identifies malarial parasites (P. falciparum, P. vivax) in blood through antigen detection or microscopy.',
        'TYPHOID': 'Detects Salmonella typhi antibodies (Widal test) or antigen to diagnose typhoid fever.',
        'CANCER': f'Screens tumor markers including {test_names[:80]} to aid in early cancer detection and monitoring treatment response.',
        'PSA': 'Measures Prostate-Specific Antigen levels to screen for prostate cancer and monitor treatment response.',
        'ALLERGY': f'Identifies specific allergen sensitivities by measuring IgE antibodies against {test_names[:80]}.',
        'TESTOSTERONE': 'Measures total and/or free testosterone levels to evaluate male hormone status, fertility, and vitality.',
        'ESTRADIOL': 'Measures estradiol (E2) levels to evaluate ovarian function, menstrual cycle, and menopausal status.',
        'FSH': 'Measures FSH and LH hormones that regulate reproductive function, ovulation, and sperm production.',
        'PROLACTIN': 'Measures prolactin hormone levels to evaluate fertility issues, irregular periods, and pituitary function.',
        'CORTISOL': 'Measures cortisol (stress hormone) levels to diagnose adrenal disorders like Cushing\'s or Addison\'s disease.',
        'BONE': f'Evaluates bone health markers including {test_names[:80]} to assess fracture risk and bone mineral status.',
        'CALCIUM': 'Measures serum calcium, phosphorus, and related minerals to assess bone health, parathyroid function, and calcium metabolism.',
        'URIC ACID': 'Measures serum uric acid levels to diagnose gout, kidney stones risk, and purine metabolism disorders.',
        'CRP': 'Measures C-reactive protein to detect systemic inflammation associated with infections, autoimmune diseases, and cardiac risk.',
        'HOMOCYSTEINE': 'Measures homocysteine levels — an independent risk factor for heart attack, stroke, and blood clots.',
        'ELECTROLYTE': 'Measures sodium, potassium, chloride, and bicarbonate to assess fluid balance, nerve function, and kidney health.',
        'SEMEN': 'Analyses sperm count, motility, morphology, and volume to evaluate male fertility and reproductive health.',
        'AMH': 'Measures Anti-Müllerian Hormone to assess ovarian reserve and predict fertility and menopause timing.',
        'PCOD': 'Profiles hormones (LH, FSH, testosterone, insulin) to diagnose PCOD/PCOS and assess metabolic impact.',
        'PCOS': 'Evaluates hormonal and metabolic markers associated with PCOS including androgens, insulin, and lipids.',
        'TROPONIN': 'Measures cardiac troponin, a highly sensitive marker released when heart muscle is damaged, to diagnose heart attacks.',
        'PERIPHERAL': 'Examines a stained blood smear under microscope to evaluate cell shapes, sizes, and identify blood disorders.',
        'ZINC': 'Measures serum zinc levels to detect deficiency affecting immunity, wound healing, and growth.',
        'MAGNESIUM': 'Measures serum magnesium critical for muscle function, nerve transmission, and heart rhythm.',
        'FOLATE': 'Measures folic acid levels essential for DNA synthesis, neural tube health, and red blood cell formation.',
    }

    what = None
    for kw, text in what_map.items():
        if kw in name_u:
            what = text
            break
    if not what:
        what = f'Analyses {len(tests) if tests else "key"} parameters including {test_names[:100]} to evaluate health status and detect abnormalities.'

    # ---- WHO SHOULD TAKE ----
    who_map = {
        'HEMOGRAM': 'Anyone experiencing fatigue, weakness, frequent infections, or as part of routine annual check-up.',
        'URINE': 'Individuals with urinary symptoms, burning sensation, frequent urination, or diabetes monitoring.',
        'TSH': 'Anyone with unexplained weight changes, fatigue, hair loss, cold/heat intolerance, or family history of thyroid disorders.',
        'THYROID': 'People with fatigue, weight fluctuations, mood swings, hair thinning, or a family history of thyroid disease.',
        'LIPID': 'Adults over 20 years, especially those with obesity, diabetes, smoking habit, or family history of heart disease.',
        'LIVER': 'Individuals with alcohol consumption, jaundice, abdominal pain, medication use, or liver disease risk.',
        'KIDNEY': 'People with diabetes, hypertension, swelling, reduced urine output, or family history of kidney disease.',
        'VITAMIN D': 'Indoor workers, people with bone pain, frequent illness, depression, or limited sun exposure.',
        'VITAMIN B12': 'Vegetarians, vegans, elderly individuals, or those with fatigue, tingling hands/feet, or memory issues.',
        'IRON': 'Women of reproductive age, pregnant women, vegetarians, or anyone with fatigue or pale skin.',
        'HBA1C': 'Diabetic patients for monitoring, prediabetics, and adults with obesity or family history of diabetes.',
        'DIABETES': 'Overweight adults, those with family history of diabetes, hypertension patients, or anyone above 40.',
        'CARDIAC': 'Adults over 30, smokers, diabetics, hypertension patients, or those with family history of heart disease.',
        'CHOLESTEROL': 'Adults over 20 years, especially men over 35, women over 45, diabetics, and heart disease risk individuals.',
        'HIV': 'Sexually active individuals, those with multiple partners, healthcare workers, or anyone seeking routine STD screening.',
        'HEPATITIS': 'Healthcare workers, blood transfusion recipients, IV drug users, or anyone with unexplained liver symptoms.',
        'DENGUE': 'Individuals with sudden high fever, severe headache, body aches, or rash during monsoon season.',
        'MALARIA': 'Anyone with cyclical fever, chills, sweating, especially after travel to endemic regions.',
        'TYPHOID': 'Individuals with prolonged fever (>3 days), abdominal pain, or recent consumption of contaminated food/water.',
        'CANCER': 'Individuals over 40 with family history of cancer, unexplained weight loss, or as routine cancer screening.',
        'PSA': 'Men over 50, or over 40 with family history of prostate cancer, or those with urinary symptoms.',
        'ALLERGY': 'People with chronic sneezing, itching, skin rashes, asthma, or unexplained allergic reactions.',
        'TESTOSTERONE': 'Men with low libido, erectile dysfunction, fatigue, reduced muscle mass, or mood changes.',
        'FSH': 'Women with irregular periods, infertility concerns, or men with low sperm count.',
        'PROLACTIN': 'Women with irregular periods, milky discharge, infertility, or anyone with suspected pituitary issues.',
        'CORTISOL': 'Individuals with unexplained weight gain, stretch marks, high blood pressure, or chronic stress.',
        'BONE': 'Post-menopausal women, elderly individuals, those on steroids, or anyone with frequent fractures.',
        'CALCIUM': 'Individuals with muscle cramps, bone pain, kidney stones, or suspected parathyroid disorders.',
        'URIC ACID': 'People with joint pain (especially big toe), gout, kidney stones, or high purine diet.',
        'CRP': 'Anyone with suspected infection, autoimmune disease, or cardiovascular risk assessment.',
        'SEMEN': 'Couples trying to conceive, men with fertility concerns, or post-vasectomy verification.',
        'AMH': 'Women planning pregnancy, those with PCOS, or anyone wanting to assess fertility/ovarian reserve.',
        'PCOD': 'Women with irregular periods, acne, excessive hair growth, weight gain, or difficulty conceiving.',
        'WOMEN': 'Women of all ages seeking comprehensive health monitoring tailored to female physiology.',
        'FULL BODY': 'All adults above 18 as part of annual preventive health check-up regardless of symptoms.',
        'SENIOR': 'Adults above 50 for comprehensive age-appropriate health monitoring and early disease detection.',
    }

    who = None
    for kw, text in who_map.items():
        if kw in name_u:
            who = text
            break
    if not who:
        if category == 'hormones':
            who = 'Individuals with hormonal imbalance symptoms, reproductive concerns, or metabolic disorders.'
        elif category == 'vitamins':
            who = 'People with nutritional deficiencies, fatigue, restricted diets, or malabsorption conditions.'
        elif category == 'heart':
            who = 'Adults with cardiac risk factors, family history of heart disease, or routine cardiovascular screening.'
        elif category == 'std':
            who = 'Sexually active individuals, those seeking routine STD screening, or pre-marital health checkups.'
        elif category == 'cancer':
            who = 'Adults above 40 as part of cancer screening, or those with risk factors or family history.'
        elif category == 'monsoon fever':
            who = 'Anyone with fever, body aches, or symptoms during or after monsoon season.'
        else:
            who = 'Adults seeking health monitoring, those with risk factors, or as part of routine annual checkup.'

    # ---- WHY DOCTORS RECOMMEND ----
    why_map = {
        'HEMOGRAM': 'A complete blood count is the most fundamental diagnostic test — it reveals anemia, infections, clotting issues, and serious blood disorders in a single draw.',
        'URINE': 'Urine analysis is a painless, non-invasive window into kidney health, urinary infections, diabetes, and liver function simultaneously.',
        'TSH': 'TSH is the single best screening marker for thyroid dysfunction — even subtle changes affect weight, mood, heart rate, and fertility.',
        'THYROID': 'Thyroid disorders are among the most underdiagnosed conditions; early detection prevents irreversible damage to heart, bones, and brain.',
        'LIPID': 'Dyslipidemia is silent until a heart attack strikes — regular lipid profiling enables timely intervention to prevent 80% of cardiovascular events.',
        'LIVER': 'The liver performs 500+ vital functions; early detection of elevated enzymes allows treatment before irreversible cirrhosis or liver failure sets in.',
        'KIDNEY': 'Kidney disease is asymptomatic until 70% function is lost — early screening with creatinine and GFR can halt progression completely.',
        'VITAMIN D': 'Vitamin D deficiency is linked to osteoporosis, depression, autoimmunity, and increased cancer risk — supplementation reverses most effects when caught early.',
        'VITAMIN B12': 'B12 deficiency causes irreversible nerve damage if untreated — early detection protects brain function, energy levels, and red blood cell production.',
        'IRON': 'Iron deficiency is the world\'s most common nutritional disorder — identifying it early prevents anemia, cognitive decline, and pregnancy complications.',
        'HBA1C': 'HbA1c reflects true long-term glucose control far better than fasting glucose — it\'s the gold standard for diabetes diagnosis and management.',
        'CARDIAC': 'Cardiovascular disease kills 1 in 3 adults — this panel identifies multiple simultaneous risk factors allowing personalized, preventive intervention.',
        'CHOLESTEROL': 'Statins and lifestyle changes reduce cardiac events by 35-50% — but only if elevated cholesterol is identified first through regular screening.',
        'HIV': 'Early HIV diagnosis allows ART therapy that reduces viral load to undetectable — patients live normal lifespans and stop transmission to others.',
        'HEPATITIS': 'Hepatitis B and C often progress silently for decades before causing liver failure or cancer — early treatment can achieve complete viral suppression.',
        'DENGUE': 'Dengue can rapidly progress to severe hemorrhagic fever — NS1 antigen detection in the first 5 days allows timely hospitalization and care.',
        'MALARIA': 'Untreated malaria causes cerebral complications within hours — rapid antigen testing enables same-day treatment to prevent life-threatening outcomes.',
        'CANCER': 'Tumor markers enable cancer detection at Stage 1 when 90%+ cure rates are achievable — far superior to late-stage diagnosis.',
        'PSA': 'PSA screening reduces prostate cancer mortality by 20-30% when combined with digital rectal exam in men over 50.',
        'ALLERGY': 'Identifying specific allergen triggers allows targeted avoidance and immunotherapy — dramatically improving quality of life without guesswork.',
        'TESTOSTERONE': 'Low testosterone is linked to depression, osteoporosis, and metabolic syndrome — supplementation under medical guidance restores vitality and health.',
        'FSH': 'FSH and LH are gatekeepers of reproductive health — their levels guide fertility treatment decisions and identify hormonal causes of infertility.',
        'CORTISOL': 'Cortisol imbalance underlies many unexplained symptoms including central obesity, hypertension, and immune dysfunction that won\'t resolve without diagnosis.',
        'BONE': 'Silent osteoporosis affects 1 in 3 women over 50 — bone density screening identifies it before the first fracture causes irreversible disability.',
        'URIC ACID': 'Hyperuricemia causes gout attacks and kidney stones — dietary changes and urate-lowering therapy are highly effective when detected early.',
        'HOMOCYSTEINE': 'Elevated homocysteine doubles heart attack risk independently of cholesterol — B vitamin supplementation effectively normalizes levels when identified.',
        'SEMEN': 'Male factor infertility accounts for 50% of couples\' conception difficulties — semen analysis provides the definitive diagnosis to guide treatment.',
        'AMH': 'AMH is the most reliable predictor of ovarian reserve — it guides IVF protocol selection and helps women make informed family planning decisions.',
        'PCOD': 'PCOS affects 10% of women globally and increases risk of diabetes, infertility, and endometrial cancer — early hormonal profiling enables effective management.',
        'FULL BODY': 'Annual full-body checks identify risk factors 5-10 years before disease onset — preventive intervention at this stage saves lives and healthcare costs.',
    }

    why = None
    for kw, text in why_map.items():
        if kw in name_u:
            why = text
            break
    if not why:
        if category == 'std':
            why = 'STDs are frequently asymptomatic — routine screening breaks transmission chains and enables early treatment before complications arise.'
        elif category == 'allergy':
            why = 'Precise allergen identification enables targeted avoidance strategies that dramatically reduce allergic reactions.'
        elif category == 'monsoon fever':
            why = 'Monsoon fevers share overlapping symptoms — pathogen-specific testing guides correct treatment and prevents misdiagnosis.'
        elif category == 'cancer':
            why = 'Cancer caught early is almost always treatable — tumor markers provide the critical early warning system doctors rely on.'
        elif category == 'vitamins':
            why = 'Micronutrient deficiencies silently impair immunity, energy, and cognition — targeted supplementation guided by testing maximises benefit.'
        else:
            why = f'Evidence-based testing of {len(tests) if tests else "key"} markers provides actionable data for personalised preventive and therapeutic decisions.'

    return what, who, why


# ──────────────────────────────────────────────────────────────
# SHORT DESCRIPTION (one-liner summary)
# ──────────────────────────────────────────────────────────────
def get_short_description(name: str, tests: list) -> str:
    count = len(tests) if tests else 1
    return f'{name.title()} — {count} parameter{"s" if count > 1 else ""} analysed for accurate health insights.'


# ──────────────────────────────────────────────────────────────
# ESCAPE helper
# ──────────────────────────────────────────────────────────────
def esc(s):
    if s is None:
        return 'NULL'
    return "'" + str(s).replace("'", "''") + "'"


# ──────────────────────────────────────────────────────────────
# GENERATE SQL
# ──────────────────────────────────────────────────────────────
lines = []

# ALTER TABLE statements for new columns
lines.append("-- ============================================================")
lines.append("-- ALTER TABLE: add new columns if they don't exist")
lines.append("-- ============================================================")
lines.append("ALTER TABLE `test-web`.`thyrocare_products`")
lines.append("  ADD COLUMN IF NOT EXISTS `thyrocare_price` DECIMAL(10,2) NULL COMMENT '40% above selling price',")
lines.append("  ADD COLUMN IF NOT EXISTS `what_this_test_checks` TEXT NULL,")
lines.append("  ADD COLUMN IF NOT EXISTS `who_should_take_this_test` TEXT NULL,")
lines.append("  ADD COLUMN IF NOT EXISTS `why_doctors_recommend` TEXT NULL;")
lines.append("")
lines.append("-- ============================================================")
lines.append("-- INSERT / UPSERT all Thyrocare products")
lines.append("-- ============================================================")
lines.append("")

stats = {}

for i, p in enumerate(data, 1):
    thyrocare_id = p['id']
    name         = p['name']
    ptype        = p.get('type', 'SSKU')
    tests        = [t['name'] for t in p.get('testsIncluded', [])]
    no_tests     = len(tests) if tests else p.get('noOfTestsIncluded', 1)

    rate              = p.get('rate', {})
    listing_price     = float(rate.get('listingPrice', 0) or 0)
    selling_price     = float(rate.get('sellingPrice', 0) or 0)
    discount_pct      = float(rate.get('discountPercentage', 0) or 0)
    notational_inc    = float(rate.get('notationalIncentive', 0) or 0)
    thyrocare_price   = round(selling_price * 1.40, 2)

    bene              = p.get('beneficiaries', {})
    bene_min          = int(bene.get('min', 1) or 1)
    bene_max          = int(bene.get('max', 10) or 10)
    bene_multiple     = int(bene.get('multiple', 1) or 1)

    flags             = p.get('flags', {})
    is_fasting        = 1 if flags.get('isFastingRequired') else 0
    is_home           = 1 if flags.get('isHomeCollectible') else 0

    category          = get_category(name, tests)
    about             = get_about(name)[:50]
    short_desc        = get_short_description(name, tests)
    what, who, why    = get_test_info(name, tests, category)

    stats[category]   = stats.get(category, 0) + 1

    sql = (
        f"INSERT INTO `test-web`.`thyrocare_products` "
        f"(`thyrocare_id`, `name`, `type`, `no_of_tests_included`, "
        f"`listing_price`, `selling_price`, `discount_percentage`, `notational_incentive`, "
        f"`beneficiaries_min`, `beneficiaries_max`, `beneficiaries_multiple`, "
        f"`is_fasting_required`, `is_home_collectible`, "
        f"`about`, `short_description`, `category`, "
        f"`thyrocare_price`, `what_this_test_checks`, `who_should_take_this_test`, `why_doctors_recommend`, "
        f"`is_active`, `is_deleted`, `created_at`, `updated_at`) "
        f"VALUES ("
        f"{esc(thyrocare_id)}, {esc(name)}, {esc(ptype)}, {no_tests}, "
        f"{listing_price}, {selling_price}, {discount_pct}, {notational_inc}, "
        f"{bene_min}, {bene_max}, {bene_multiple}, "
        f"{is_fasting}, {is_home}, "
        f"{esc(about)}, {esc(short_desc)}, {esc(category)}, "
        f"{thyrocare_price}, {esc(what)}, {esc(who)}, {esc(why)}, "
        f"1, 0, '{NOW}', '{NOW}'"
        f")\n"
        f"ON DUPLICATE KEY UPDATE "
        f"`name`=VALUES(`name`), `type`=VALUES(`type`), "
        f"`no_of_tests_included`=VALUES(`no_of_tests_included`), "
        f"`listing_price`=VALUES(`listing_price`), `selling_price`=VALUES(`selling_price`), "
        f"`discount_percentage`=VALUES(`discount_percentage`), "
        f"`notational_incentive`=VALUES(`notational_incentive`), "
        f"`beneficiaries_min`=VALUES(`beneficiaries_min`), "
        f"`beneficiaries_max`=VALUES(`beneficiaries_max`), "
        f"`beneficiaries_multiple`=VALUES(`beneficiaries_multiple`), "
        f"`is_fasting_required`=VALUES(`is_fasting_required`), "
        f"`is_home_collectible`=VALUES(`is_home_collectible`), "
        f"`about`=VALUES(`about`), `short_description`=VALUES(`short_description`), "
        f"`category`=VALUES(`category`), "
        f"`thyrocare_price`=VALUES(`thyrocare_price`), "
        f"`what_this_test_checks`=VALUES(`what_this_test_checks`), "
        f"`who_should_take_this_test`=VALUES(`who_should_take_this_test`), "
        f"`why_doctors_recommend`=VALUES(`why_doctors_recommend`), "
        f"`updated_at`='{NOW}';"
    )
    lines.append(f"-- [{i}] {name}")
    lines.append(sql)
    lines.append("")

# Summary
lines.append("-- ============================================================")
lines.append("-- CATEGORY DISTRIBUTION SUMMARY")
for cat, cnt in sorted(stats.items(), key=lambda x: -x[1]):
    lines.append(f"--   {cat:<35} {cnt} products")
lines.append("-- ============================================================")

output = '\n'.join(lines)
with open('thyrocare_products_insert.sql', 'w', encoding='utf-8') as f:
    f.write(output)

print(f"Generated {len(data)} INSERT statements")
print("Category breakdown:")
for cat, cnt in sorted(stats.items(), key=lambda x: -x[1]):
    print(f"  {cat:<35} {cnt}")
print("Output: thyrocare_products_insert.sql")
