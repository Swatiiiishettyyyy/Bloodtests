#!/usr/bin/env python3
"""
Fix thyrocare_products_insert.sql:
  1. name  → proper display alias (Title Case, expand abbreviations)
  2. about → meaningful emoji caption
  3. thyrocare_price → round(selling_price * 1.4, 2)
"""

import re, sys

# ─── abbreviation expansion ───────────────────────────────────────────────────
ABBR = {
    'CBC': 'Complete Blood Count',
    'LFT': 'Liver Function Tests',
    'KFT': 'Kidney Function Tests',
    'RFT': 'Renal Function Tests',
    'TFT': 'Thyroid Function Tests',
    'PFT': 'Pulmonary Function Tests',
    'TSH': 'TSH (Thyroid Stimulating Hormone)',
    'USTSH': 'Ultrasensitive TSH',
    'PBS': 'Peripheral Blood Smear',
    'HBA1C': 'HbA1c (Glycated Haemoglobin)',
    'HOMA': 'HOMA Insulin Resistance',
    'AMH': 'Anti-Müllerian Hormone',
    'FSH': 'FSH (Follicle Stimulating Hormone)',
    'LH': 'LH (Luteinising Hormone)',
    'PRL': 'Prolactin',
    'DHEA': 'DHEA (Dehydroepiandrosterone)',
    'SHBG': 'SHBG (Sex Hormone Binding Globulin)',
    'CRP': 'CRP (C-Reactive Protein)',
    'ESR': 'ESR (Erythrocyte Sedimentation Rate)',
    'RA': 'Rheumatoid Arthritis',
    'ANA': 'ANA (Anti-Nuclear Antibody)',
    'PSA': 'PSA (Prostate Specific Antigen)',
    'AFP': 'AFP (Alpha-Fetoprotein)',
    'CEA': 'CEA (Carcinoembryonic Antigen)',
    'CA125': 'CA-125',
    'CA19': 'CA 19-9',
    'HIV': 'HIV',
    'HCV': 'Hepatitis C',
    'HBsAG': 'Hepatitis B Surface Antigen',
    'VDRL': 'VDRL (Syphilis Screen)',
    'TORCH': 'TORCH Infections Panel',
    'PCR': 'PCR Test',
    'CBC': 'Complete Blood Count',
    'RBC': 'Red Blood Cells',
    'WBC': 'White Blood Cells',
    'TIBC': 'TIBC (Total Iron Binding Capacity)',
    'GFR': 'GFR (Glomerular Filtration Rate)',
    'BUN': 'BUN (Blood Urea Nitrogen)',
    'SGOT': 'SGOT',
    'SGPT': 'SGPT',
    'ALP': 'ALP (Alkaline Phosphatase)',
    'GGT': 'GGT (Gamma Glutamyl Transferase)',
    'HDL': 'HDL Cholesterol',
    'LDL': 'LDL Cholesterol',
    'VLDL': 'VLDL Cholesterol',
    'T3': 'T3 (Triiodothyronine)',
    'T4': 'T4 (Thyroxine)',
    'FT3': 'Free T3',
    'FT4': 'Free T4',
    'INSULIN': 'Insulin',
    'IGF': 'IGF (Insulin-like Growth Factor)',
    'CORTISOL': 'Cortisol',
    'TESTOSTERONE': 'Testosterone',
    'ESTRADIOL': 'Estradiol',
    'PROGESTERONE': 'Progesterone',
}

def to_alias(thyrocare_id: str) -> str:
    """Convert THYROCARE_ID → Proper Display Name."""
    s = thyrocare_id.strip()
    # Try a few direct nice mappings first
    direct = {
        'HEMOGRAM - 6 PART (DIFF)': 'Hemogram – 6 Part Differential',
        'COMPLETE URINE ANALYSIS': 'Complete Urine Analysis',
        'T3-T4-USTSH': 'Thyroid Panel – T3, T4 & Ultrasensitive TSH',
        'LIPID PROFILE': 'Lipid Profile – Cholesterol & Fats',
        'LIVER FUNCTION TESTS': 'Liver Function Test (LFT)',
        'ELEMENTS 22 (TOXIC AND NUTRIENTS)': 'Elements 22 – Toxic Metals & Nutrients',
        'KIDPRO': 'KidPro – Kidney Function Profile',
        'ROUTINE URINE ANALYSIS': 'Routine Urine Analysis',
        'IRON DEFICIENCY PROFILE': 'Iron Deficiency Profile',
        'WOMEN BASIC PROFILE WITH UTSH': 'Women\'s Basic Health Profile with Ultrasensitive TSH',
        'CARDIAC RISK MARKERS': 'Cardiac Risk Markers Panel',
        'SERUM ELECTROLYTES': 'Serum Electrolytes (Na, K, Cl)',
        'ADVANCED RENAL PROFILE': 'Advanced Renal (Kidney) Profile',
        'URINOGRAM': 'Urinogram – Urine Dipstick Panel',
        'WOMEN ADVANCED PROFILE WITH UTSH': 'Women\'s Advanced Health Profile with TSH',
        'DOCTOR RECOMMENDED FULL BODY CHECKUP BASIC': 'Doctor-Recommended Full Body Checkup – Basic',
        'HEALTHY PROFILE 2': 'Healthy Profile 2 – Comprehensive Wellness',
        'PERIPHERAL BLOOD SMEAR (PBS)': 'Peripheral Blood Smear',
        'SMOKERS PANEL - BASIC': 'Smokers Health Panel – Basic',
        'USTSH-LH-FSH-PRL': 'Hormonal Panel – TSH, LH, FSH & Prolactin',
        'VITAMIN D TOTAL AND B12 COMBO': 'Vitamin D & B12 Combo',
        'BLOOD GROUPING AND RH TYPING': 'Blood Group & Rh Typing',
        'URINE PROTEIN CREATININE RATIO': 'Urine Protein–Creatinine Ratio',
        'WIDAL': 'Widal Test – Typhoid Antibodies',
        'COMPLETE VITAMINS PROFILE': 'Complete Vitamins Profile',
        'COMPREHENSIVE HEART HEALTH CHECKUP': 'Comprehensive Heart Health Checkup',
        'VITAMIN D PROFILE': 'Vitamin D Profile',
        'MALARIAL ANTIGEN': 'Malaria Antigen Test',
        'AMH ADVANCED PROFILE': 'AMH (Anti-Müllerian Hormone) Advanced Profile',
        'BETA-THALASSEMIA SCREENING': 'Beta-Thalassemia Screening',
        'QUANTIFERON -TB GOLD PLUS': 'QuantiFERON-TB Gold Plus (Tuberculosis)',
        'FEVER PANEL - BASIC': 'Fever Panel – Basic Infection Screen',
        'GASTRO / GUT HEALTH PANEL': 'Gastro & Gut Health Panel',
        'HEALTHY PROFILE 1': 'Healthy Profile 1 – Essential Wellness',
        'TYPHOID TEST': 'Typhoid Test',
        'FEVER PANEL - ADVANCED': 'Fever Panel – Advanced Infection Screen',
        'ARTHRITIS PROFILE ADVANCED': 'Arthritis Profile – Advanced',
        'VITAMIN B COMPLEX PROFILE': 'Vitamin B Complex Profile',
        'HEPATITIS B PROFILE': 'Hepatitis B Profile',
        'FEVER PROFILE': 'Fever Profile',
        'HOMA INSULIN RESISTANCE INDEX': 'HOMA Insulin Resistance Index',
        'AMINO ACID PROFILE (35)': 'Amino Acid Profile – 35 Markers',
        'ARTHRITIS PROFILE BASIC': 'Arthritis Profile – Basic',
        'HEPATITIS PANEL': 'Hepatitis Panel',
        'CANCER MARKER BASIC': 'Cancer Markers – Basic Screening',
        'CANCER MARKER ADVANCED': 'Cancer Markers – Advanced Screening',
        'DIABETES CARE PROFILE': 'Diabetes Care Profile',
        'HBA1C': 'HbA1c – Glycated Haemoglobin',
        'BLOOD SUGAR FASTING': 'Fasting Blood Sugar',
        'BLOOD SUGAR PP': 'Post-Prandial Blood Sugar',
        'BLOOD SUGAR RANDOM': 'Random Blood Sugar',
        'THYROID PROFILE TOTAL': 'Thyroid Profile – Total (T3, T4, TSH)',
        'THYROID PROFILE FREE': 'Thyroid Profile – Free (FT3, FT4, TSH)',
        'VITAMIN B12': 'Vitamin B12 (Cobalamin)',
        'VITAMIN D (25-OH)': 'Vitamin D (25-OH)',
        'CALCIUM': 'Serum Calcium',
        'PHOSPHORUS': 'Serum Phosphorus',
        'URIC ACID': 'Uric Acid',
        'SERUM CREATININE': 'Serum Creatinine',
        'SERUM PROTEIN ELECTROPHORESIS': 'Serum Protein Electrophoresis',
        'PSA - TOTAL': 'PSA Total (Prostate Cancer Marker)',
        'PSA TOTAL AND FREE RATIO': 'PSA Total & Free Ratio',
        'CA 125': 'CA-125 (Ovarian Cancer Marker)',
        'CA 19.9': 'CA 19-9 (Pancreatic Cancer Marker)',
        'CEA': 'CEA (Colorectal Cancer Marker)',
        'AFP': 'AFP (Liver Cancer Marker)',
        'HIV 1&2 ANTIBODY': 'HIV 1 & 2 Antibody Screening',
        'HEPATITIS C ANTIBODY': 'Hepatitis C Antibody (Anti-HCV)',
        'HBsAG': 'Hepatitis B Surface Antigen (HBsAg)',
        'VDRL': 'VDRL – Syphilis Screening',
        'DENGUE NS1 ANTIGEN': 'Dengue NS1 Antigen',
        'DENGUE IgG/IgM': 'Dengue IgG & IgM Antibodies',
        'MALARIA PARASITE': 'Malaria Parasite Detection',
        'TESTOSTERONE TOTAL': 'Total Testosterone',
        'CORTISOL MORNING': 'Morning Cortisol (Stress Hormone)',
        'FSH': 'FSH – Follicle Stimulating Hormone',
        'LH': 'LH – Luteinising Hormone',
        'PROLACTIN': 'Prolactin',
        'ESTRADIOL': 'Estradiol (E2)',
        'PROGESTERONE': 'Progesterone',
        'INSULIN FASTING': 'Fasting Insulin',
        'HOMOCYSTEINE': 'Homocysteine',
        'CRP - HS': 'High-Sensitivity CRP (hs-CRP)',
        'ESR': 'ESR (Erythrocyte Sedimentation Rate)',
        'RA FACTOR': 'Rheumatoid Arthritis (RA) Factor',
        'ANA (HEP2)': 'ANA (Anti-Nuclear Antibody)',
        'ANTI CCP ANTIBODY': 'Anti-CCP (Rheumatoid Arthritis Marker)',
        'URINE MICROALBUMIN': 'Urine Microalbumin (Kidney Damage Marker)',
        'STOOL ROUTINE': 'Stool Routine Examination',
        'COMPLETE STOOL ANALYSIS': 'Complete Stool Analysis',
        'H. PYLORI ANTIGEN STOOL': 'H. Pylori Antigen (Stool)',
        'H. PYLORI IGG': 'H. Pylori IgG Antibody',
        'VITAMIN A': 'Vitamin A (Retinol)',
        'VITAMIN E': 'Vitamin E (Tocopherol)',
        'ZINC': 'Serum Zinc',
        'MAGNESIUM': 'Serum Magnesium',
        'COPPER': 'Serum Copper',
        'SELENIUM': 'Serum Selenium',
        'FERRITIN': 'Serum Ferritin (Iron Stores)',
        'TRANSFERRIN': 'Serum Transferrin',
        'SERUM IRON': 'Serum Iron',
        'FOLATE': 'Folate (Vitamin B9)',
        'BETA HCG': 'Beta-hCG (Pregnancy Hormone)',
        'TORCH PANEL': 'TORCH Infections Panel',
        'RUBELLA IGG/IGM': 'Rubella IgG & IgM',
        'CMV IGG/IGM': 'CMV (Cytomegalovirus) IgG & IgM',
        'HERPES SIMPLEX 1&2': 'Herpes Simplex Virus 1 & 2',
    }
    if s in direct:
        return direct[s]
    # Generic: title-case with clean-ups
    result = s.title()
    result = result.replace(' - ', ' – ')
    result = result.replace('(Diff)', '(Differential)')
    result = result.replace('Ustsh', 'Ultrasensitive TSH')
    result = result.replace('Utsh', 'Ultrasensitive TSH')
    result = result.replace('Hba1C', 'HbA1c')
    result = result.replace('Hbsag', 'HBsAg')
    result = result.replace(' Pbs)', ' PBS)')
    result = result.replace('Ck-Mb', 'CK-MB')
    result = result.replace('Ldl', 'LDL')
    result = result.replace('Hdl', 'HDL')
    result = result.replace('Vldl', 'VLDL')
    result = result.replace('Rbc', 'RBC')
    result = result.replace('Wbc', 'WBC')
    result = result.replace('Crp', 'CRP')
    result = result.replace('Esr', 'ESR')
    result = result.replace('Psa', 'PSA')
    result = result.replace('Afp', 'AFP')
    result = result.replace('Cea', 'CEA')
    result = result.replace('Pcr', 'PCR')
    result = result.replace('Tsh', 'TSH')
    result = result.replace('Fsh', 'FSH')
    result = result.replace(' Lh ', ' LH ')
    result = result.replace('Amh', 'AMH')
    result = result.replace('Dhea', 'DHEA')
    result = result.replace('Shbg', 'SHBG')
    result = result.replace('Igf', 'IGF')
    result = result.replace('Hiv', 'HIV')
    result = result.replace('Hcv', 'HCV')
    result = result.replace(' Ana ', ' ANA ')
    result = result.replace('Tibc', 'TIBC')
    result = result.replace('Gfr', 'GFR')
    result = result.replace('Bun', 'BUN')
    result = result.replace('Sgot', 'SGOT')
    result = result.replace('Sgpt', 'SGPT')
    result = result.replace('Alp', 'ALP')
    result = result.replace('Ggt', 'GGT')
    return result


def get_about(name: str, category: str, what_checks: str) -> str:
    """Generate a good short about caption with emoji."""
    n = name.upper()
    c = category.lower()

    # Blood / CBC / Hemogram
    if any(x in n for x in ['HEMOGRAM', 'CBC', 'COMPLETE BLOOD', 'BLOOD COUNT', 'DIFFERENTIAL']):
        return 'Essential test for complete blood cell health & immunity assessment'
    if 'PERIPHERAL BLOOD SMEAR' in n or 'PBS' in n:
        return 'Microscopic blood smear analysis to identify abnormal blood cells'
    if 'BLOOD GROUP' in n or 'RH TYP' in n:
        return 'Know your blood group and Rh factor – essential for emergencies'
    if 'BLOOD SUGAR FASTING' in n or ('BLOOD SUGAR' in n and 'FAST' in n):
        return 'Fasting blood glucose to screen and monitor diabetes'
    if 'BLOOD SUGAR' in n and 'PP' in n:
        return 'Post-meal blood sugar to evaluate glucose metabolism'
    if 'BLOOD SUGAR' in n:
        return 'Blood glucose measurement for diabetes screening & monitoring'
    if 'HBA1C' in n or 'GLYCATED' in n or 'GLYCOSYLATED' in n:
        return '3-month average blood sugar control – gold standard for diabetes management'

    # Thyroid
    if any(x in n for x in ['T3-T4', 'THYROID PROFILE', 'T3,T4', 'T3 T4']):
        return 'Complete thyroid hormone panel – T3, T4, and TSH in one test'
    if 'THYROID' in n or 'TSH' in n or 'USTSH' in n or 'UTSH' in n:
        return 'Thyroid function screening to detect hypothyroidism or hyperthyroidism'
    if 'T3' in n and 'T4' in n:
        return 'Thyroid hormone levels assessment for metabolism & energy regulation'

    # Liver
    if any(x in n for x in ['LIVER FUNCTION', 'LFT', 'HEPATIC']):
        return 'Comprehensive liver enzyme & function panel for early liver disease detection'
    if 'HEPATITIS B PROFILE' in n:
        return 'Complete Hepatitis B infection & immunity status panel'
    if 'HEPATITIS' in n and 'PANEL' in n:
        return 'Multi-marker hepatitis screening for B and C virus infections'
    if 'HEPATITIS B' in n or 'HBSAG' in n:
        return 'Hepatitis B surface antigen test to detect active infection'
    if 'HEPATITIS C' in n or 'HCV' in n or 'ANTI-HCV' in n:
        return 'Hepatitis C antibody screening for liver infection'
    if 'JAUNDICE' in n or 'BILIRUBIN' in n:
        return 'Bilirubin measurement for jaundice diagnosis and monitoring'

    # Kidney
    if any(x in n for x in ['RENAL', 'KIDNEY', 'KFT', 'RFT']):
        return 'Comprehensive kidney function test to detect early renal disease'
    if 'KIDPRO' in n:
        return 'Essential kidney health markers – creatinine, urea & electrolytes'
    if 'CREATININE' in n and 'URINE' not in n:
        return 'Serum creatinine to assess kidney filtration efficiency'
    if 'URIC ACID' in n:
        return 'Uric acid measurement to screen for gout and kidney stones'
    if 'ELECTROLYTE' in n or 'SODIUM' in n or 'POTASSIUM' in n:
        return 'Body electrolyte balance – sodium, potassium & chloride assessment'
    if 'MICROALBUMIN' in n:
        return 'Urine microalbumin – early marker of kidney damage in diabetes'
    if 'URINE PROTEIN CREATININE' in n:
        return 'Urine protein-creatinine ratio to quantify kidney protein loss'

    # Urine
    if 'URINOGRAM' in n:
        return 'Urine dipstick panel for rapid kidney & metabolic health screen'
    if 'COMPLETE URINE' in n or 'URINE ANALYSIS' in n:
        return 'Detailed urine examination for kidney, bladder & metabolic health'
    if 'ROUTINE URINE' in n:
        return 'Routine urine test to screen for infections, kidney & metabolic issues'
    if 'URINE' in n:
        return 'Urine examination for urinary tract & metabolic health assessment'

    # Lipid / Heart
    if 'LIPID PROFILE' in n:
        return 'Cholesterol & triglyceride panel to assess cardiovascular disease risk'
    if any(x in n for x in ['CARDIAC RISK', 'HEART HEALTH', 'CARDIOVASCULAR']):
        return 'Advanced cardiac risk markers to prevent heart attack & stroke'
    if 'COMPREHENSIVE HEART' in n:
        return 'Full cardiac health checkup – cholesterol, enzymes & inflammation markers'
    if any(x in n for x in ['LIPID', 'CHOLESTEROL', 'TRIGLYCERIDE']):
        return 'Blood fat & cholesterol assessment for heart health monitoring'
    if any(x in n for x in ['TROPONIN', 'CK-MB', 'CKMBMASS']):
        return 'Cardiac enzyme markers to detect and monitor heart muscle damage'
    if 'SMOKERS PANEL' in n:
        return 'Key health markers to assess smoking-related organ damage risks'
    if 'HOMOCYSTEINE' in n:
        return 'Homocysteine – independent risk marker for heart disease & stroke'

    # Vitamins & Minerals
    if 'VITAMIN D' in n and 'B12' in n:
        return 'Vitamin D & B12 combo – essential for bones, nerves & energy'
    if 'VITAMIN D' in n:
        return 'Vitamin D assessment for bone health, immunity & mood regulation'
    if 'VITAMIN B12' in n or ('VITAMIN' in n and 'B12' in n):
        return 'Vitamin B12 test to detect deficiency causing fatigue & nerve damage'
    if 'VITAMIN B COMPLEX' in n:
        return 'Complete B-vitamin profile for energy, nerves & cell health'
    if 'COMPLETE VITAMINS' in n or 'VITAMIN PROFILE' in n:
        return 'Comprehensive vitamin screen to identify nutritional deficiencies'
    if 'IRON DEFICIENCY' in n:
        return 'Iron stores & anaemia panel – ferritin, serum iron & TIBC'
    if 'FERRITIN' in n:
        return 'Serum ferritin to assess iron stores and detect iron deficiency'
    if 'FOLATE' in n or 'FOLIC ACID' in n:
        return 'Folate (Vitamin B9) test – essential for cell growth & pregnancy'
    if 'ELEMENTS 22' in n or 'TOXIC' in n:
        return 'Comprehensive trace elements panel – essential nutrients & toxic metals'
    if 'CALCIUM' in n and 'VITAMIN' not in n:
        return 'Serum calcium for bone health, nerve and muscle function'
    if 'MAGNESIUM' in n:
        return 'Magnesium level check for muscle, nerve & heart health'
    if 'ZINC' in n:
        return 'Serum zinc to evaluate immune function & wound healing capacity'
    if 'AMINO ACID' in n:
        return 'Amino acid profile to assess protein metabolism & genetic metabolic disorders'

    # Hormones & Fertility
    if 'AMH' in n:
        return 'Anti-Müllerian Hormone – best marker for ovarian reserve & fertility'
    if 'WOMEN' in n and ('BASIC' in n or 'ADVANCED' in n):
        return 'Comprehensive women\'s wellness panel – hormones, thyroid & key metabolic markers'
    if any(x in n for x in ['USTSH-LH-FSH', 'LH-FSH', 'FSH-LH']):
        return 'Reproductive hormone panel – LH, FSH, prolactin & TSH'
    if 'TESTOSTERONE' in n:
        return 'Testosterone assessment for hormonal health in men and women'
    if 'CORTISOL' in n:
        return 'Cortisol measurement to evaluate adrenal function & stress response'
    if 'PROLACTIN' in n:
        return 'Prolactin test for fertility, breast health & pituitary function'
    if 'ESTRADIOL' in n or 'OESTRADIOL' in n:
        return 'Estradiol (E2) assessment for reproductive & hormonal health'
    if 'PROGESTERONE' in n:
        return 'Progesterone test for ovulation, luteal phase & pregnancy health'
    if 'DHEA' in n:
        return 'DHEA-S test for adrenal gland function & hormonal balance'
    if 'SHBG' in n:
        return 'SHBG measurement to evaluate sex hormone availability & metabolic health'
    if 'INSULIN' in n and 'RESISTANCE' not in n and 'HOMA' not in n:
        return 'Fasting insulin to detect insulin resistance and pre-diabetes'
    if 'HOMA' in n or 'INSULIN RESISTANCE' in n:
        return 'HOMA-IR index – precise measurement of insulin resistance severity'
    if 'HORMONE' in n:
        return 'Hormonal panel to detect imbalance affecting energy, mood & fertility'

    # Diabetes / Metabolic
    if 'DIABETES' in n:
        return 'Comprehensive diabetes management panel – glucose, HbA1c & kidney markers'
    if 'GLUCOSE' in n:
        return 'Blood glucose measurement for diabetes screening and monitoring'

    # Inflammation / Autoimmune
    if 'ARTHRITIS PROFILE' in n and 'ADVANCED' in n:
        return 'Advanced arthritis markers panel – RA factor, CRP, anti-CCP & more'
    if 'ARTHRITIS PROFILE' in n:
        return 'Key arthritis & joint inflammation markers in one convenient panel'
    if 'CRP' in n and 'HS' in n:
        return 'High-sensitivity CRP – precise cardiovascular & systemic inflammation marker'
    if 'CRP' in n:
        return 'C-Reactive Protein to detect active inflammation or infection'
    if 'ESR' in n:
        return 'ESR test to detect systemic inflammation and monitor chronic diseases'
    if 'RA FACTOR' in n or 'RHEUMATOID' in n:
        return 'Rheumatoid arthritis factor to aid diagnosis of inflammatory joint disease'
    if 'ANA' in n and 'ANTIBODY' in n:
        return 'ANA screening for autoimmune diseases like lupus and rheumatoid arthritis'
    if 'ANTI CCP' in n:
        return 'Anti-CCP antibody – highly specific marker for rheumatoid arthritis'

    # Cancer Markers
    if 'CANCER MARKER' in n and 'ADVANCED' in n:
        return 'Advanced cancer marker panel for multi-organ early detection screening'
    if 'CANCER MARKER' in n:
        return 'Key cancer screening markers to aid early detection across organs'
    if 'PSA' in n:
        return 'PSA test for prostate cancer screening in men over 40'
    if 'CA 125' in n or 'CA125' in n:
        return 'CA-125 ovarian cancer marker for screening and monitoring'
    if 'CA 19' in n:
        return 'CA 19-9 pancreatic & GI cancer screening marker'
    if 'CEA' in n:
        return 'CEA cancer marker for colorectal & other cancer screening'
    if 'AFP' in n:
        return 'Alpha-fetoprotein (AFP) – liver & testicular cancer marker'

    # Infections / Fever
    if 'FEVER PANEL' in n and 'ADVANCED' in n:
        return 'Advanced fever panel to identify bacterial, viral & parasitic causes'
    if 'FEVER PANEL' in n:
        return 'Essential fever panel to rapidly identify common infection causes'
    if 'FEVER PROFILE' in n:
        return 'Fever work-up panel including malaria, typhoid & blood count markers'
    if 'TYPHOID' in n or 'WIDAL' in n:
        return 'Typhoid fever antibody test (Widal) for Salmonella infection detection'
    if 'DENGUE NS1' in n:
        return 'Dengue NS1 antigen – early acute dengue fever detection test'
    if 'DENGUE' in n:
        return 'Dengue virus infection detection using antigen and antibody markers'
    if 'MALARIA' in n or 'MALARIAL' in n:
        return 'Rapid malaria antigen test to detect Plasmodium infection'
    if 'QUANTIFERON' in n or 'TB GOLD' in n:
        return 'QuantiFERON-TB Gold – accurate tuberculosis latent infection test'
    if 'HEPATITIS' in n:
        return 'Hepatitis infection screening with comprehensive antibody & antigen markers'
    if 'HIV' in n:
        return 'HIV 1 & 2 antibody screening – confidential and highly accurate'
    if 'VDRL' in n or 'SYPHILIS' in n:
        return 'VDRL test for syphilis infection screening'
    if 'TORCH' in n:
        return 'TORCH infections panel – essential screen in pregnancy planning'
    if 'H. PYLORI' in n or 'HELICOBACTER' in n:
        return 'H. pylori infection test for stomach ulcers & gastric cancer risk'

    # Stool / Gut
    if 'STOOL' in n or 'GUT HEALTH' in n or 'GASTRO' in n:
        return 'Gut health & stool analysis to detect infections & digestive disorders'

    # Full body / Comprehensive
    if 'FULL BODY' in n or 'COMPREHENSIVE' in n or 'COMPLETE HEALTH' in n:
        return 'Complete full-body health checkup covering all major organ systems'
    if 'HEALTHY PROFILE' in n:
        return 'Multi-system wellness panel covering blood, organs & essential health markers'
    if 'PALEO PROFILE' in n:
        return 'Paleo & metabolic health panel assessing nutritional & organ biomarkers'
    if 'DOCTOR RECOMMENDED' in n:
        return 'Doctor-recommended preventive checkup to detect hidden health risks early'

    # Miscellaneous
    if 'THALASSEMIA' in n or 'HAEMOGLOBIN' in n or 'HEMOGLOBIN' in n:
        return 'Haemoglobin disorder screen for thalassemia and anaemia evaluation'
    if 'CD3' in n or 'CD4' in n or 'CD8' in n or 'LYMPHOCYTE' in n:
        return 'Immune cell count panel to assess lymphocyte subsets & immunity status'
    if 'DRUG PANEL' in n or 'SUBSTANCE' in n:
        return 'Drug abuse screening panel for urine-based substance detection'
    if 'SERUM PROTEIN ELECTROPHORESIS' in n:
        return 'Serum protein electrophoresis to detect myeloma & protein disorders'
    if 'IGF' in n:
        return 'IGF-1 (growth factor) test for growth hormone status assessment'
    if 'VITAMIN A' in n:
        return 'Vitamin A (retinol) assessment for vision, skin & immunity health'
    if 'VITAMIN E' in n:
        return 'Vitamin E (tocopherol) antioxidant status assessment'
    if 'RUBELLA' in n:
        return 'Rubella IgG & IgM – immunity & infection status in pregnancy'
    if 'CMV' in n:
        return 'Cytomegalovirus (CMV) antibody test – infection & immunity check'
    if 'HERPES' in n:
        return 'Herpes simplex virus antibody screening for HSV-1 & HSV-2'
    if 'BETA HCG' in n or 'B-HCG' in n or 'HCG' in n:
        return 'Beta-hCG pregnancy hormone test – also used as tumour marker'
    if 'PREGNANC' in n:
        return 'Pregnancy monitoring panel for maternal & foetal health'
    if 'PRE-OPERATIVE' in n or 'PREOPERATIVE' in n or 'PRE OPERATIVE' in n:
        return 'Essential pre-surgery health screening panel for safe anaesthesia & surgery'
    if 'UREA' in n:
        return 'Blood urea nitrogen measurement for kidney & protein metabolism'

    # fallback using category
    cat_about = {
        'essential tests': 'Essential diagnostic test for routine health screening',
        'heart': 'Key cardiac health marker for cardiovascular risk assessment',
        'liver': 'Liver health assessment marker for early disease detection',
        'kidney': 'Renal health marker to assess kidney function',
        'hormones': 'Hormonal health marker for metabolic & endocrine assessment',
        'vitamins': 'Nutritional health marker to identify deficiency or excess',
        'diabetes': 'Diabetes monitoring marker for glucose & metabolic health',
        'cancer': 'Cancer screening marker for early detection & monitoring',
        'infection': 'Infection & immunity marker for disease detection',
        'arthritis': 'Joint & autoimmune marker for inflammatory disease screening',
    }
    for k, v in cat_about.items():
        if k in c:
            return v

    return f'Diagnostic test covering {n.title()} – accurate lab-based health assessment'


# ─── SQL line processor ────────────────────────────────────────────────────────

def parse_sql_values(values_str: str):
    """Parse a SQL VALUES (...) string into a list of raw value tokens."""
    tokens = []
    i = 0
    s = values_str.strip()
    if s.startswith('('):
        s = s[1:]
    if s.endswith(')'):
        s = s[:-1]

    while i < len(s):
        # skip whitespace
        while i < len(s) and s[i] == ' ':
            i += 1
        if i >= len(s):
            break
        if s[i] == "'":
            # string token
            j = i + 1
            val_chars = []
            while j < len(s):
                if s[j] == "'" and j + 1 < len(s) and s[j+1] == "'":
                    val_chars.append("'")
                    j += 2
                elif s[j] == "'":
                    j += 1
                    break
                else:
                    val_chars.append(s[j])
                    j += 1
            tokens.append(('str', ''.join(val_chars)))
            i = j
            # skip comma
            while i < len(s) and s[i] in (' ', ','):
                i += 1
        elif s[i] in '-0123456789':
            j = i
            while j < len(s) and s[j] not in (',',):
                j += 1
            tokens.append(('num', s[i:j].strip()))
            i = j
            while i < len(s) and s[i] in (' ', ','):
                i += 1
        elif s[i:i+4] == 'NULL':
            tokens.append(('null', 'NULL'))
            i += 4
            while i < len(s) and s[i] in (' ', ','):
                i += 1
        else:
            j = i
            while j < len(s) and s[j] != ',':
                j += 1
            tokens.append(('raw', s[i:j].strip()))
            i = j
            while i < len(s) and s[i] in (' ', ','):
                i += 1
    return tokens


def token_to_sql(t):
    typ, val = t
    if typ == 'str':
        return "'" + val.replace("'", "''") + "'"
    elif typ == 'null':
        return 'NULL'
    else:
        return val


def process_line(line: str) -> str:
    """Process a single INSERT line and return updated line."""
    if not line.strip().upper().startswith('INSERT INTO'):
        return line

    # Find VALUES (...)
    m = re.search(r'\bVALUES\s*\(', line, re.IGNORECASE)
    if not m:
        return line

    val_start = m.end() - 1  # position of '('
    # Find matching closing paren - need to count nesting
    depth = 0
    val_end = val_start
    i = val_start
    while i < len(line):
        if line[i] == '(':
            depth += 1
        elif line[i] == ')':
            depth -= 1
            if depth == 0:
                val_end = i
                break
        elif line[i] == "'":
            # skip string
            i += 1
            while i < len(line):
                if line[i] == "'" and i + 1 < len(line) and line[i+1] == "'":
                    i += 2
                elif line[i] == "'":
                    break
                else:
                    i += 1
        i += 1

    values_content = line[val_start:val_end+1]  # includes ( and )
    tokens = parse_sql_values(values_content)

    # Column order (0-indexed):
    # 0:thyrocare_id, 1:name, 2:type, 3:no_of_tests_included,
    # 4:listing_price, 5:selling_price, 6:discount_percentage, 7:notational_incentive,
    # 8:beneficiaries_min, 9:beneficiaries_max, 10:beneficiaries_multiple,
    # 11:is_fasting_required, 12:is_home_collectible,
    # 13:about, 14:short_description, 15:category, 16:thyrocare_price,
    # 17:what_this_test_checks, 18:who_should_take_this_test, 19:why_doctors_recommend,
    # 20:is_active, 21:is_deleted, 22:created_at, 23:updated_at

    if len(tokens) < 24:
        return line  # can't parse safely

    thyrocare_id = tokens[0][1]
    selling_price_str = tokens[5][1]
    category = tokens[15][1] if tokens[15][0] == 'str' else ''
    what_checks = tokens[17][1] if tokens[17][0] == 'str' else ''

    # 1. Fix name alias
    new_name = to_alias(thyrocare_id)
    tokens[1] = ('str', new_name)

    # 2. Fix about caption
    new_about = get_about(thyrocare_id, category, what_checks)
    tokens[13] = ('str', new_about)

    # 3. Fix thyrocare_price = round(selling_price * 1.4, 2)
    try:
        sp = float(selling_price_str)
        new_tp = round(sp * 1.4, 2)
        tokens[16] = ('num', str(new_tp))
    except ValueError:
        pass

    # Rebuild VALUES
    new_vals = '(' + ', '.join(token_to_sql(t) for t in tokens) + ')'
    new_line = line[:val_start] + new_vals + line[val_end+1:]
    return new_line


def main():
    in_file = 'thyrocare_products_insert.sql'
    out_file = 'thyrocare_products_insert.sql'

    with open(in_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    out = []
    updated = 0
    for line in lines:
        new_line = process_line(line)
        if new_line != line:
            updated += 1
        out.append(new_line)

    with open(out_file, 'w', encoding='utf-8') as f:
        f.writelines(out)

    print(f"Done. Updated {updated} INSERT lines.")


if __name__ == '__main__':
    main()
