// Treatment page — Acupoint reference data (EN/HE) for the point cards and the point panel.
// Phase 4.3 replaces these literals with /api/acupoints.
// Classic script: shares globals with the other treatment/*.js files, loaded in order.

// ── Point reference data ───────────────────────────────────────────────────────
const POINT_INFO = {
  'ST36': { name: 'Zusanli', channel: 'Stomach', location: '3 cun below ST35, one finger-width lateral to the anterior crest of the tibia', actions: 'Tonifies Qi and Blood, strengthens digestion, boosts immunity, calms Shen' },
  'LI4':  { name: 'Hegu', channel: 'Large Intestine', location: 'Dorsum of the hand, between 1st and 2nd metacarpal bones, midpoint of 2nd metacarpal', actions: 'Clears Wind and Heat, stops pain, promotes labour (contraindicated in pregnancy)' },
  'PC6':  { name: 'Neiguan', channel: 'Pericardium', location: '2 cun above the wrist crease, between palmaris longus and flexor carpi radialis', actions: 'Regulates Heart, calms the mind, stops nausea, opens the chest' },
  'LR3':  { name: 'Taichong', channel: 'Liver', location: 'Dorsum of foot, depression between 1st and 2nd metatarsal bones', actions: 'Moves Liver Qi, subdues Liver Yang, regulates menstruation, calms Wind' },
  'SP6':  { name: 'Sanyinjiao', channel: 'Spleen', location: '3 cun above the medial malleolus, on the posterior border of the tibia', actions: 'Strengthens Spleen and Stomach, nourishes Blood and Yin, regulates menstruation' },
  'GV20': { name: 'Baihui', channel: 'Governing Vessel', location: 'On the midline of the head, at the intersection with the line connecting the ear apices', actions: 'Clears the mind, lifts the spirit, raises Yang Qi, benefits the brain' },
  'HT7':  { name: 'Shenmen', channel: 'Heart', location: 'Wrist, radial side of flexor carpi ulnaris tendon, at the proximal border of the pisiform', actions: 'Calms Shen, tonifies Heart Qi and Blood, treats insomnia and anxiety' },
  'KD3':  { name: 'Taixi', channel: 'Kidney', location: 'Depression between the medial malleolus and the Achilles tendon', actions: 'Tonifies Kidney Yin and Yang, strengthens the lower back, benefits the brain' },
  'GB20': { name: 'Fengchi', channel: 'Gallbladder', location: 'Depression between upper sternocleidomastoid and trapezius, level with GV16', actions: 'Expels Wind, clears the head and eyes, relieves headache' },
  'GB21': { name: 'Jianjing', channel: 'Gallbladder', location: 'Highest point of the shoulder, midway between GV14 and the acromion', actions: 'Relaxes sinews, descends rebellious Qi, relieves shoulder/neck tension (avoid in pregnancy)' },
  'TE5':  { name: 'Waiguan', channel: 'Triple Energizer', location: '2 cun above wrist crease on dorsum of forearm, between radius and ulna', actions: 'Releases Exterior Wind-Heat, opens Yang Linking Vessel, relieves earache and migraines' },
  'BL23': { name: 'Shenshu', channel: 'Bladder', location: '1.5 cun lateral to GV4, level with lower border of L2 spinous process', actions: 'Tonifies Kidney Qi/Yang, strengthens lumbar region, benefits ears and bones' },
  'BL40': { name: 'Weizhong', channel: 'Bladder', location: 'Midpoint of popliteal crease, between biceps femoris and semitendinosus', actions: 'Activates the channel, relieves lower back pain and sciatica, clears summer heat' },
  'CV6':  { name: 'Qihai', channel: 'Conception Vessel', location: '1.5 cun below the umbilicus', actions: 'Tonifies Original Qi, warms and strengthens Yang, regulates menstruation' },
  'CV12': { name: 'Zhongwan', channel: 'Conception Vessel', location: '4 cun above the umbilicus (midway between CV8 and xiphoid)', actions: 'Tonifies Spleen and Stomach, resolves Damp, descends rebellious Stomach Qi' },
  'CV17': { name: 'Shanzhong', channel: 'Conception Vessel', location: 'Anterior midline at the 4th intercostal space, between the nipples', actions: 'Opens the chest, regulates Qi, descends rebellious Lung Qi, calms the Shen' },
  'GV14': { name: 'Dazhui', channel: 'Governing Vessel', location: 'Midline below C7 spinous process', actions: 'Releases Exterior, clears Heat, regulates Yang of the whole body' },
  'LU7':  { name: 'Lieque', channel: 'Lung', location: '1.5 cun above wrist crease on radial side, cleft above styloid process', actions: 'Releases Exterior Wind, descends and disseminates Lung Qi, opens Conception Vessel' },
  'LI11': { name: 'Quchi', channel: 'Large Intestine', location: 'Lateral end of cubital crease, midway between LU5 and lateral epicondyle', actions: 'Clears Heat, cools Blood, resolves Damp, regulates Qi and Blood' },
  'SP9':  { name: 'Yinlingquan', channel: 'Spleen', location: 'Depression on lower border of medial condyle of the tibia', actions: 'Resolves Dampness, regulates the Lower Burner, opens water passages' },
  'ST40': { name: 'Fenglong', channel: 'Stomach', location: '8 cun below ST35, two finger-widths lateral to anterior crest of tibia', actions: 'Resolves Phlegm and Damp, calms the Shen, opens the chest, clears Heat' },
  'YIN':  { name: 'Yintang', channel: 'Extra Point', location: 'Midline of forehead, midway between medial ends of eyebrows', actions: 'Calms the Shen, brightens the eyes, relieves frontal headache, supports sleep' },
  'YINTANG': { name: 'Yintang', channel: 'Extra Point', location: 'Midline of forehead, midway between medial ends of eyebrows', actions: 'Calms the Shen, brightens the eyes, relieves frontal headache, supports sleep' },
};

const POINT_INFO_HE = {
  'ST36': { name: 'זוסנלי', channel: 'קיבה', location: '3 צון מתחת ל-ST35, רוחב אצבע לרוחב הקצה הקדמי של הטיביה', actions: 'מחזק צ׳י ודם, מחזק עיכול, מעלה חסינות, מרגיע שן' },
  'LI4':  { name: 'הגו', channel: 'המעי הגדול', location: 'גב היד, בין עצמות המטקרפל הראשונה לשנייה, אמצע המטקרפל השנייה', actions: 'מנקה רוח וחום, עוצר כאב, מזרז לידה (אסור בהריון)' },
  'PC6':  { name: 'ניגואן', channel: 'קרום הלב', location: '2 צון מעל פרק כף היד, בין גידי הפלמריס לונגוס ופלקסור קרפי רדיאליס', actions: 'מווסת לב, מרגיע נפש, עוצר בחילה, פותח חזה' },
  'LR3':  { name: 'טאיצ׳ונג', channel: 'כבד', location: 'גב כף הרגל, שקע בין עצמות המטטרסל הראשונה לשנייה', actions: 'מזיז צ׳י כבד, מדכא יאנג כבד, מווסת מחזור, מרגיע רוח' },
  'SP6':  { name: 'סניינג׳יאו', channel: 'טחול', location: '3 צון מעל הקרסול המדיאלי, לאורך הגבול האחורי של הטיביה', actions: 'מחזק טחול וקיבה, מזין דם ויין, מווסת מחזור' },
  'GV20': { name: 'בייהוי', channel: 'כלי השלטון', location: 'על קו האמצע של הראש, בצומת עם הקו המחבר את קצות האוזניים', actions: 'מנקה נפש, מרים רוח, מעלה יאנג צ׳י, מועיל למוח' },
  'HT7':  { name: 'שנמן', channel: 'לב', location: 'פרק כף היד, צד רדיאלי של גיד פלקסור קרפי אולנריס, גבול פרוקסימלי של עצם הפיזיפורם', actions: 'מרגיע שן, מחזק צ׳י ודם של הלב, מטפל בנדודי שינה וחרדה' },
  'KD3':  { name: 'טאישי', channel: 'כליה', location: 'שקע בין הקרסול המדיאלי לגיד אכילס', actions: 'מחזק יין ויאנג של הכליה, מחזק גב תחתון, מועיל למוח' },
  'GB20': { name: 'פנגצ׳י', channel: 'כיס המרה', location: 'שקע בין שרירי SCM לטרפז, ברמת GV16', actions: 'מגרש רוח, מנקה ראש ועיניים, מקל על כאב ראש' },
  'GB21': { name: 'ג׳יאנג׳ינג', channel: 'כיס המרה', location: 'נקודת השיא של הכתף, אמצע בין GV14 לאקרומיון', actions: 'מרפה גידים, מוריד צ׳י מורד, מקל על מתח כתף וצוואר (הימנע בהריון)' },
  'TE5':  { name: 'וואיגואן', channel: 'שלושת האנרגיזרים', location: '2 צון מעל פרק כף היד על גב הזרוע, בין הרדיוס לאולנה', actions: 'משחרר רוח-חום חיצוני, פותח כלי יאנג מחבר, מקל על כאב אוזניים ומיגרנות' },
  'BL23': { name: 'שנשו', channel: 'שלפוחית השתן', location: '1.5 צון לרוחב GV4, ברמת הגבול התחתון של עמוד שדרה L2', actions: 'מחזק צ׳י/יאנג של הכליה, מחזק אזור המותניים, מועיל לאוזניים ולעצמות' },
  'BL40': { name: 'ווייז׳ונג', channel: 'שלפוחית השתן', location: 'אמצע קפל הפופליאל, בין ביצפס פמוריס לסמיטנדינוסוס', actions: 'מפעיל ערוץ, מקל על כאב גב תחתון וסיאטיקה, מנקה חום קיץ' },
  'CV6':  { name: 'צ׳יהאי', channel: 'כלי ההריון', location: '1.5 צון מתחת לטבור', actions: 'מחזק צ׳י המקור, מחמם ומחזק יאנג, מווסת מחזור' },
  'CV12': { name: 'ג׳ונגוואן', channel: 'כלי ההריון', location: '4 צון מעל הטבור (אמצע בין CV8 ל-xiphoid)', actions: 'מחזק טחול וקיבה, פותר לחות, מוריד צ׳י קיבה מורד' },
  'CV17': { name: 'שנג׳ונג', channel: 'כלי ההריון', location: 'קו אמצע קדמי ברמת הרווח הבין-צלעי הרביעי, בין הפטמות', actions: 'פותח חזה, מווסת צ׳י, מוריד צ׳י ריאה מורד, מרגיע שן' },
  'GV14': { name: 'דאז׳וי', channel: 'כלי השלטון', location: 'קו אמצע מתחת לעמוד שדרה C7', actions: 'משחרר חיצוני, מנקה חום, מווסת יאנג של כל הגוף' },
  'LU7':  { name: 'ליקווה', channel: 'ריאה', location: '1.5 צון מעל פרק כף היד על הצד הרדיאלי, מעל תהליך הסטילואיד', actions: 'משחרר רוח חיצוני, מוריד ומפיץ צ׳י ריאה, פותח כלי ההריון' },
  'LI11': { name: 'צ׳וצ׳י', channel: 'המעי הגדול', location: 'קצה לרוחב של קפל המרפק, אמצע בין LU5 לאפיקונדיל לרוחב', actions: 'מנקה חום, מצנן דם, פותר לחות, מווסת צ׳י ודם' },
  'SP9':  { name: 'יינלינגצ׳ואן', channel: 'טחול', location: 'שקע על הגבול התחתון של הקונדיל המדיאלי של הטיביה', actions: 'פותר לחות, מווסת בורר תחתון, פותח ערוצי מים' },
  'ST40': { name: 'פנגלונג', channel: 'קיבה', location: '8 צון מתחת ל-ST35, שני רוחבי אצבע לרוחב קצה הטיביה הקדמית', actions: 'פותר ליחה ולחות, מרגיע שן, פותח חזה, מנקה חום' },
  'YIN':  { name: 'יינטנג', channel: 'נקודה נוספת', location: 'קו אמצע המצח, אמצע בין הקצות המדיאליות של הגבות', actions: 'מרגיע שן, מאיר עיניים, מקל על כאב ראש חזיתי, תומך בשינה' },
  'YINTANG': { name: 'יינטנג', channel: 'נקודה נוספת', location: 'קו אמצע המצח, אמצע בין הקצות המדיאליות של הגבות', actions: 'מרגיע שן, מאיר עיניים, מקל על כאב ראש חזיתי, תומך בשינה' },
};

// The form every list on the page stores a code in: upper case, no spaces or dashes ("st-36" → "ST36").
function normPointCode(code) {
  return String(code ?? '').toUpperCase().replace(/[\s-]/g, '');
}

// The key a code has in POINT_INFO. The table uses KD for Kidney where the WHO code is KI (KI3).
function pointInfoKey(code) {
  const c = normPointCode(code);
  if (POINT_INFO[c]) return c;
  const kidney = c.replace(/^KI(?=\d)/, 'KD');
  return POINT_INFO[kidney] ? kidney : c;
}

function getPointInfo(code) {
  return (_ZF_LANG === 'he' ? POINT_INFO_HE : POINT_INFO)[pointInfoKey(code)] || {};
}

// The English channel name, whatever the page language: it picks the colour theme.
function pointChannel(code) {
  return (POINT_INFO[pointInfoKey(code)] || {}).channel || '';
}
