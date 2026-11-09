"""Domain vocabulary and regular expressions for Indian college and university websites."""
from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Departments. Canonical name -> phrases that point to it (lower case).
DEPARTMENTS: dict[str, tuple[str, ...]] = {
    "Computer Science and Engineering": ("computer science", "cse", "c.s.e", "computer engineering", "computer sc",
                                         "computing", "software engineering"),
    "Artificial Intelligence and Data Science": ("artificial intelligence", "ai & ds", "ai&ds", "aids", "ai and ds",
                                                 "data science", "machine learning", "ai & ml", "aiml", "ai&ml"),
    "Information Technology": ("information technology", "i.t.", "dept of it", "department of it"),
    "Computer Applications": ("computer applications", "mca", "bca"),
    "Electronics and Communication Engineering": ("electronics and communication", "electronics & communication", "ece",
                                                  "e.c.e", "electronics and telecommunication", "e&tc", "entc",
                                                  "electronics & telecommunication", "electronics engineering", "vlsi"),
    "Electrical and Electronics Engineering": ("electrical and electronics", "electrical & electronics", "eee",
                                               "electrical engineering", "power systems"),
    "Instrumentation Engineering": ("instrumentation", "eie", "electronics and instrumentation"),
    "Mechanical Engineering": ("mechanical", "mech", "production engineering", "automobile", "mechatronics",
                               "industrial engineering", "manufacturing"),
    "Civil Engineering": ("civil", "structural engineering", "environmental engineering", "geotechnical"),
    "Chemical Engineering": ("chemical engineering", "petroleum", "polymer"),
    "Biotechnology": ("biotechnology", "bio-technology", "biotech", "bioinformatics", "biomedical engineering"),
    "Aerospace Engineering": ("aerospace", "aeronautical", "aeronautics"),
    "Architecture": ("architecture", "planning"),
    "Mathematics": ("mathematics", "maths", "applied mathematics", "statistics"),
    "Physics": ("physics", "applied physics"),
    "Chemistry": ("chemistry", "applied chemistry"),
    "Humanities and Social Sciences": ("humanities", "english", "social sciences", "economics", "hss", "languages"),
    "Management": ("management", "mba", "business administration", "commerce", "school of business", "pgdm", "bba",
                   "finance", "marketing", "human resource", "hr", "operations", "strategy",
                   "organizational behaviour", "decision sciences", "public systems", "agribusiness",
                   "entrepreneurship", "supply chain", "international business", "business analytics",
                   "fintech", "retail", "hospitality", "general management"),
    "Pharmacy": ("pharmacy", "pharmaceutical", "pharmaceutics", "pharmacology"),
    "Law": ("law", "legal studies", "llb", "llm"),
}

# Words that make a person teaching faculty (title or department line).
TEACHING_WORDS = re.compile(
    r"\b(?:professor|prof\b|lecturer|faculty|teaching|hod\b|head of (?:the )?department|dean|principal|"
    r"reader|tutor|adjunct|emeritus|research scientist|scientist|post[- ]?doc)", re.I)
# Staff who are not teaching faculty (often listed on department 'people' pages).
NON_TEACHING = re.compile(
    r"\b(?:lab(?:oratory)? (?:assistant|attendant|technician|instructor)|technician|technical assistant|attendant|"
    r"clerk|office assistant|steno|typist|peon|driver|accountant|librarian|library assistant|store keeper|"
    r"system administrator|programmer|data entry|helper|watchman|security|caretaker|warden|mts|"
    r"multi[- ]tasking|non[- ]teaching)\b", re.I)

# --------------------------------------------------------------------------
# Qualifications. Dotted forms are normalised before matching.
QUALIFICATIONS = [
    "PhD", "Ph.D", "MPhil", "MTech", "ME", "MS", "MSc", "MCA", "MBA", "PGDM", "MCom", "MA", "MArch", "MPharm", "LLM",
    "BTech", "BE", "BSc", "BCA", "BCom", "BA", "BArch", "BPharm", "LLB", "PDF", "PostDoc", "NET", "SET", "SLET",
    "GATE", "JRF", "SRF", "FIE", "MIEEE", "SMIEEE", "MISTE", "DSc", "DLitt", "MEd", "BEd", "PGDCA", "AMIE",
]
_QUAL_ALT = "|".join(sorted((re.escape(q) for q in QUALIFICATIONS), key=len, reverse=True))
QUAL_RE = re.compile(rf"(?<![A-Za-z])({_QUAL_ALT})(?![A-Za-z])")
# Tokens that make a line a qualification line even alone. 'ME', 'MS', 'MA', 'BA', 'BE', 'NET', 'SET', 'PDF'
# are ordinary words too, so they count only next to a strong one.
QUAL_STRONG = {"PhD", "MTech", "BTech", "MPhil", "MSc", "MCA", "MBA", "PGDM", "MCom", "MArch", "MPharm", "LLM",
               "BSc", "BCA", "BCom", "BArch", "BPharm", "LLB", "PostDoc", "DSc", "DLitt", "MEd", "BEd", "PGDCA"}
WEAK_QUALS = {"ME", "MS", "MA", "BA", "BE", "NET", "SET", "SLET", "PDF", "GATE", "JRF", "SRF", "FIE", "AMIE",
              "MIEEE", "SMIEEE", "MISTE"}

DOTTED_QUALS = [
    (re.compile(r"Ph\.\s*D\.?", re.I), "PhD"),
    (re.compile(r"(?<![A-Za-z])M\.\s*Phil\.?", re.I), "MPhil"),
    (re.compile(r"(?<![A-Za-z])M\.\s*Tech\.?", re.I), "MTech"),
    (re.compile(r"(?<![A-Za-z])B\.\s*Tech\.?", re.I), "BTech"),
    (re.compile(r"(?<![A-Za-z])M\.\s*E\.?(?=[\s,;(/]|$)"), "ME"),
    (re.compile(r"(?<![A-Za-z])B\.\s*E\.?(?=[\s,;(/]|$)"), "BE"),
    (re.compile(r"(?<![A-Za-z])M\.\s*S\.(?=[\s,(/]|$)"), "MS"),
    (re.compile(r"(?<![A-Za-z])M\.\s*Sc\.?", re.I), "MSc"),
    (re.compile(r"(?<![A-Za-z])B\.\s*Sc\.?", re.I), "BSc"),
    (re.compile(r"(?<![A-Za-z])M\.\s*C\.\s*A\.?", re.I), "MCA"),
    (re.compile(r"(?<![A-Za-z])M\.\s*B\.\s*A\.?", re.I), "MBA"),
    (re.compile(r"(?<![A-Za-z])M\.\s*Com\.?", re.I), "MCom"),
    (re.compile(r"(?<![A-Za-z])B\.\s*Com\.?", re.I), "BCom"),
    (re.compile(r"(?<![A-Za-z])M\.\s*A\.(?=[\s,(/]|$)"), "MA"),
    (re.compile(r"(?<![A-Za-z])M\.\s*Pharm\.?", re.I), "MPharm"),
    (re.compile(r"(?<![A-Za-z])Post[- ]?Doc(?:toral)?(?: Fellow(?:ship)?)?", re.I), "PostDoc"),
]


def normalize_quals(text: str) -> str:
    for rx, rep in DOTTED_QUALS:
        text = rx.sub(rep, text)
    return text


# --------------------------------------------------------------------------
# Names. Faculty pages write 'Dr.', 'Prof.', and also 'Mr.', 'Ms.', 'Mrs.', 'Shri', 'Smt.'.
HONORIFIC = (r"(?:Dr|DR|Prof|PROF|Professor|PROFESSOR|Mr|MR|Mrs|MRS|Ms|MS|Miss|Shri|Sri|Smt|Kum|Er|"
             r"Doctor(?=\s))")
_RANK = r"(?:Brig|Col|Lt\.?\s*Col|Maj|Gen|Capt|Cdr|Retd|Prof|Dr)"
_PREFIX_EXTRA = rf"(?:\(\s*{_RANK}\.?\s*(?:\(?retd\.?\)?)?\s*\)\s*|{_RANK}\.?\s+|{HONORIFIC}(?![A-Za-z])(?:\.\s*|\s+))"
NAME_TOKEN = r"(?:\.(?=[A-Z]\.))?[A-Z](?:[a-zA-Z'’\-]|\.(?=[A-Z]))*\.?"
NAME_RE = re.compile(
    rf"(?<![A-Za-z]){HONORIFIC}(?![A-Za-z])\.?\s*{_PREFIX_EXTRA}{{0,3}}"
    rf"((?:{NAME_TOKEN}\s+){{0,4}}{NAME_TOKEN})"
)
NAME_STOP = {
    "Professor", "Associate", "Assistant", "Adjunct", "Visiting", "Emeritus", "Lecturer", "Senior", "Head", "HOD",
    "Dean", "Director", "Principal", "Department", "Dept", "Faculty", "School", "College", "University", "Institute",
    "PhD", "MTech", "BTech", "ME", "MCA", "MBA", "MSc", "Engineering", "Science", "Sciences", "Technology",
    "Computer", "Electrical", "Electronics", "Mechanical", "Civil", "Chemical", "Information", "Mathematics",
    "Physics", "Chemistry", "Management", "Humanities", "Research", "Profile", "View", "Read", "More", "Know",
    "Contact", "Email", "Phone", "Ext", "Office", "Room", "Cabin", "Block", "In", "At", "Of", "And", "The", "Is",
    "Has", "With", "Her", "His", "He", "She", "Who", "Was", "For", "To", "On", "Our", "Name", "Details", "Search",
    "Team", "Staff", "Members", "Coordinator", "Officer", "Training", "Placement", "Placements", "Cell", "Club",
    "Chapter", "Alumni", "Students", "Student", "Experience", "Years", "Areas", "Interest", "Interests",
    "Specialization", "Specialisation", "Qualification", "Qualifications", "Designation", "Publications", "Google",
    "Scholar", "Orcid", "Scopus", "Vidwan", "Linkedin", "Home", "Page", "Here", "Now", "Today", "Programme", "Program",
    "Trust", "Foundation", "Award", "Retd",
    # Site navigation and page headings that look like two capitalised words ("About Us", "Organization Chart").
    "About", "Us", "Organization", "Organisation", "Chart", "Vision", "Mission", "Message", "Desk", "Welcome",
    "History", "Gallery", "Photo", "Photos", "Download", "Downloads", "Notice", "Notices", "Circular", "Circulars",
    "Committee", "Committees", "Admission", "Admissions", "Fee", "Fees", "Structure", "Login", "Portal", "Library",
    "Hostel", "Sports", "Events", "Event", "News", "Result", "Results", "Syllabus", "Academic", "Academics",
    "Campus", "Infrastructure", "Governing", "Body", "Grievance", "Feedback", "Mandatory", "Disclosure", "Code",
    "Conduct", "Scholarship", "Scholarships", "Examination", "Exam", "Exams", "Anti", "Ragging", "Overview",
    "Objectives", "Outcomes", "Activities", "Achievements", "Recruitment", "Tender", "Tenders", "Facilities",
}
NOT_A_PERSON = {
    "award", "awards", "medal", "oration", "lecture", "memorial", "prize", "fellowship", "scholarship", "trophy",
    "excellence", "endowment", "puraskar", "samman", "foundation", "trust", "charitable", "chair", "auditorium",
    "hall", "block", "building", "library", "road", "marg", "nagar", "college", "university", "institute", "school",
}
# Section headings on a CV page, read as if they were the person's name.
HEADING_WORDS = {
    "professional", "summary", "biography", "biodata", "bio", "overview", "introduction", "objective",
    "about", "education", "educational", "experience", "experiences", "qualification", "qualifications",
    "publication", "publications", "achievement", "achievements", "responsibility", "responsibilities",
    "skills", "expertise", "membership", "memberships", "award", "awards", "patent", "patents",
    "workshop", "workshops", "seminar", "conference", "conferences", "project", "projects", "subject",
    "subjects", "teaching", "administrative", "personal", "curriculum", "vitae", "resume", "cv",
}
_NAME_STOP_LOWER = {w.lower() for w in NAME_STOP}
_QUALS_UPPER = {q.upper() for q in QUALIFICATIONS}
_ROLE_WORD = re.compile(r"(?:olog(?:y|ist|ical)|ics|ical|ering|ment)$", re.I)
_HONORIFIC_WORDS = {"dr", "prof", "professor", "doctor", "mr", "mrs", "ms", "miss", "shri", "sri", "smt", "kum", "er"}


def clean_name(raw: str) -> str | None:
    """Trim trailing non-name words and validate a captured name."""
    tokens = raw.replace("’", "'").split()
    if tokens and all(t.isupper() or len(t.strip(".")) <= 1 for t in tokens):
        tokens = [t if len(t.strip(".")) <= 1 or "." in t.strip(".") else t.capitalize() for t in tokens]
    while tokens and tokens[0].strip(".,").lower() in _HONORIFIC_WORDS:
        tokens = tokens[1:]
    if tokens and tokens[0].strip(".,()").split("-")[0].lower() in HEADING_WORDS:
        return None  # "Professional Summary", "Educational Qualifications": a heading, not a person
    out: list[str] = []
    for tok in tokens:
        bare = tok.strip(".,")
        if bare.strip("()").lower() in NOT_A_PERSON:
            return None
        if bare.split("-")[0].lower() in _NAME_STOP_LOWER and len(bare.split("-")) > 1:
            break  # "Dean-Student", "Head-Academics": a role written with a hyphen
        if bare.endswith("'s") and bare[:-2].lower() in _NAME_STOP_LOWER:
            break
        if (bare.lower() in _NAME_STOP_LOWER and not (bare.lower() == "more" and out)) or bare.upper() in _QUALS_UPPER:
            break
        if len(bare) > 5 and _ROLE_WORD.search(bare) and out:
            break
        out.append(tok.rstrip(",").lstrip("."))
    while out and len(out) > 1 and len(out[-1].strip(".")) <= 1 and not out[-1].strip(".").isupper():
        out.pop()
    name = " ".join(out).strip(" .,-")
    letters = [t for t in out if len(t.strip(".")) > 1]
    if not letters or len(name) < 3 or len(name) > 60:
        return None
    if len(out) == 1 and _ROLE_WORD.search(name):
        return None  # "Biotechnology", "Management": a subject standing alone is not a person
    return name


def honorific_of(text: str) -> str:
    """'Prof. (Dr.) A B' -> 'Dr.';  'Mrs. A B' -> 'Mrs.';  'A B' -> ''."""
    head = text[:30]
    if re.search(r"\bDr\b|\bDoctor\b", head, re.I):
        return "Dr."
    if re.search(r"\bProf(?:essor)?\b", head, re.I):
        return "Prof."
    m = re.match(r"\s*(Mrs|Mr|Ms|Miss|Shri|Sri|Smt|Kum|Er)\b", head, re.I)
    if m:
        word = m.group(1).capitalize()
        return word if word in ("Miss", "Shri", "Sri") else word + "."
    return ""


def format_name(name: str, honorific: str = "") -> str:
    return f"{honorific} {name}".strip() if honorific else name


# --------------------------------------------------------------------------
DESIGNATIONS = [
    "Vice Chancellor", "Pro Vice Chancellor", "Registrar", "Director", "Principal", "Vice Principal", "Dean",
    "Associate Dean", "Head of Department", "HOD", "Professor and Head", "Professor & Head", "Senior Professor",
    "Professor of Practice", "Distinguished Professor", "Chair Professor", "Professor Emeritus", "Emeritus Professor",
    "Professor", "Associate Professor", "Assistant Professor", "Adjunct Professor", "Visiting Professor",
    "Visiting Faculty", "Guest Faculty", "Senior Lecturer", "Lecturer", "Reader", "Teaching Assistant",
    "Research Scientist", "Post Doctoral Fellow", "Training and Placement Officer", "Placement Officer",
    "Lab Assistant", "Technical Assistant", "Lab Instructor",
]
DESIGNATION_RE = re.compile(
    r"\b(?:Pro[- ]Vice[- ]Chancellor|Vice[- ]Chancellor|Vice[- ]Principal|Principal|Registrar|Associate Dean|Dean|"
    r"Head of (?:the )?Department|HOD|"
    r"(?:Senior|Distinguished|Chair|Adjunct|Visiting|Associate|Assistant|Additional|Research|Honorary)\s+Professor|"
    r"Professor\s+(?:Emeritus|of Practice|(?:and|&)\s+Head)|Emeritus Professor|Professor|"
    r"(?:Visiting|Guest|Adjunct|Teaching)\s+Faculty|Senior Lecturer|Lecturer|Reader|Teaching Assistant|"
    r"Research (?:Scientist|Associate)|Scientist|Post[- ]?Doctoral (?:Fellow|Researcher)|"
    r"(?:(?:Assistant|Deputy|Senior|Additional)\s+)?(?:Training (?:and|&) )?Placement Officer|T\s?&\s?P Officer|TPO\b|Director|"
    r"Lab(?:oratory)? (?:Assistant|Instructor|Technician)|Technical Assistant|Technician|Librarian)\b"
    r"(?:\s*\(\s*(?:(?:Sr\.?|Senior|Selection|Sel\.?)\s*(?:Grade|Scale)?|Grade[- ]?(?:I{1,3}|[1-3])|SG|SS|Contract|Contractual|Adhoc|Ad-hoc)\s*\))?"
    r"(?:\s*(?:[-–&,:]|and|of|in|&)\s*[A-Za-z&,\-–() /]{2,90})?",
    re.I,
)

EXPERIENCE_RE = re.compile(
    r"(?:(?:experience|exp\.?)\s*(?:of|:|-|–)?\s*(?:over|more than|about|nearly|around)?\s*(\d{1,2})\s*\+?\s*(?:years|yrs))"
    r"|(?:(\d{1,2})\s*\+?\s*(?:years|yrs)\.?\s*(?:of\s+)?(?:\w+\s+){0,3}?(?:experience|exp\b))"
    r"|(?:(?:over|more than|nearly|around)\s+(\d{1,2})\s+years)",
    re.I,
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:(?:\+|00)91[\s\-]?)?(?:\(?0?\d{2,5}\)?[\s\-]?)?\d{3,5}[\s\-]?\d{4,6}")

# Academic profile links: field -> host pattern.
ACADEMIC_LINKS: dict[str, re.Pattern] = {
    "google_scholar": re.compile(r"scholar\.google\.[a-z.]+/citations\?[^#\s]*user=", re.I),
    "orcid": re.compile(r"orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[\dX]", re.I),
    "scopus_id": re.compile(r"scopus\.com/(?:authid/detail\.uri\?authorId=|author/)", re.I),
    "researchgate": re.compile(r"researchgate\.net/profile/", re.I),
    "linkedin": re.compile(r"linkedin\.com/in/", re.I),
    "vidwan": re.compile(r"vidwan\.inflibnet\.ac\.in/profile/", re.I),
}
ORCID_ID_RE = re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b")

# --------------------------------------------------------------------------
LOCALITY_CITY = {
    "Powai": "Mumbai", "Andheri": "Mumbai", "Vile Parle": "Mumbai", "Matunga": "Mumbai", "Bandra": "Mumbai",
    "Guindy": "Chennai", "Kattankulathur": "Chennai", "Vadapalani": "Chennai", "Adyar": "Chennai",
    "Hauz Khas": "New Delhi", "Dwarka": "New Delhi", "Rohini": "New Delhi", "Okhla": "New Delhi",
    "Gachibowli": "Hyderabad", "Kukatpally": "Hyderabad", "Gandipet": "Hyderabad", "Ghatkesar": "Hyderabad",
    "Dundigal": "Hyderabad", "Narsapur": "Hyderabad", "Ibrahimpatnam": "Hyderabad", "Bachupally": "Hyderabad",
    "Shamshabad": "Hyderabad", "Hayathnagar": "Hyderabad",
    "Yelahanka": "Bengaluru", "Basavanagudi": "Bengaluru", "Electronic City": "Bengaluru", "Jayanagar": "Bengaluru",
    "Shivaji Nagar": "Pune", "Akurdi": "Pune", "Hadapsar": "Pune", "Lohegaon": "Pune", "Wagholi": "Pune",
    "Salt Lake": "Kolkata", "Jadavpur": "Kolkata", "Howrah": "Kolkata",
    "Kanuru": "Vijayawada", "Tadepalligudem": "Tadepalligudem", "Bhimavaram": "Bhimavaram", "Guntur": "Guntur",
}
INDIAN_CITIES = [
    "New Delhi", "Delhi", "Gurugram", "Gurgaon", "Noida", "Greater Noida", "Ghaziabad", "Faridabad", "Mumbai",
    "Navi Mumbai", "Thane", "Pune", "Nagpur", "Nashik", "Aurangabad", "Amravati", "Kolhapur", "Sangli", "Solapur",
    "Jalgaon", "Akola", "Shegaon", "Nanded", "Latur", "Bengaluru", "Bangalore", "Mysuru", "Mysore", "Mangaluru",
    "Mangalore", "Manipal", "Hubli", "Belagavi", "Belgaum", "Chennai", "Coimbatore", "Madurai", "Trichy",
    "Tiruchirappalli", "Vellore", "Salem", "Erode", "Tirunelveli", "Hyderabad", "Secunderabad", "Warangal",
    "Karimnagar", "Visakhapatnam", "Vizag", "Vijayawada", "Tirupati", "Kakinada", "Rajahmundry", "Nellore",
    "Kurnool", "Anantapur", "Kolkata", "Durgapur", "Kharagpur", "Siliguri", "Bhubaneswar", "Cuttack", "Rourkela",
    "Ahmedabad", "Surat", "Vadodara", "Rajkot", "Gandhinagar", "Anand", "Jaipur", "Jodhpur", "Udaipur", "Kota",
    "Pilani", "Lucknow", "Kanpur", "Varanasi", "Prayagraj", "Allahabad", "Agra", "Meerut", "Gorakhpur", "Patna",
    "Ranchi", "Jamshedpur", "Dhanbad", "Bhopal", "Indore", "Gwalior", "Jabalpur", "Raipur", "Bilaspur",
    "Chandigarh", "Mohali", "Ludhiana", "Amritsar", "Jalandhar", "Patiala", "Roorkee", "Dehradun", "Shimla",
    "Jammu", "Srinagar", "Kochi", "Cochin", "Thiruvananthapuram", "Trivandrum", "Kozhikode", "Calicut",
    "Thrissur", "Guwahati", "Shillong", "Silchar", "Goa", "Puducherry", "Pondicherry", "Kurukshetra", "Hisar",
    "Rohtak", "Sonipat", "Vellore", "Warangal",
] + list(LOCALITY_CITY)
CITY_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, set(INDIAN_CITIES)), key=len, reverse=True)) + r")\b")
CITY_CANON = {"Gurgaon": "Gurugram", "Bangalore": "Bengaluru", "Bombay": "Mumbai", "Calcutta": "Kolkata",
              "Madras": "Chennai", "Cochin": "Kochi", "Trivandrum": "Thiruvananthapuram", "Mysore": "Mysuru",
              "Mangalore": "Mangaluru", "Calicut": "Kozhikode", "Allahabad": "Prayagraj", "Vizag": "Visakhapatnam",
              "Pondicherry": "Puducherry", "Trichy": "Tiruchirappalli", "Belgaum": "Belagavi", **LOCALITY_CITY}

# States, and the cities each one covers. A request for "Tamil Nadu" is a request for
# its cities: the reader was city-only, so "Tamil Nadu" read as the unknown word "Tamil".
STATES: dict[str, list[str]] = {
    "Maharashtra": ["Mumbai", "Navi Mumbai", "Thane", "Pune", "Nagpur", "Nashik", "Aurangabad", "Amravati",
                    "Kolhapur", "Sangli", "Solapur", "Jalgaon", "Akola", "Nanded", "Latur"],
    "Karnataka": ["Bengaluru", "Mysuru", "Mangaluru", "Manipal", "Hubli", "Belagavi"],
    "Tamil Nadu": ["Chennai", "Coimbatore", "Madurai", "Tiruchirappalli", "Vellore", "Salem", "Erode",
                   "Tirunelveli"],
    "Telangana": ["Hyderabad", "Secunderabad", "Warangal", "Karimnagar"],
    "Andhra Pradesh": ["Visakhapatnam", "Vijayawada", "Guntur", "Tirupati", "Kakinada", "Rajahmundry",
                       "Nellore", "Kurnool", "Anantapur"],
    "West Bengal": ["Kolkata", "Durgapur", "Kharagpur", "Siliguri", "Howrah"],
    "Odisha": ["Bhubaneswar", "Cuttack", "Rourkela"],
    "Gujarat": ["Ahmedabad", "Surat", "Vadodara", "Rajkot", "Gandhinagar", "Anand"],
    "Rajasthan": ["Jaipur", "Jodhpur", "Udaipur", "Kota", "Pilani", "Bikaner"],
    "Uttar Pradesh": ["Lucknow", "Kanpur", "Varanasi", "Prayagraj", "Agra", "Meerut", "Gorakhpur", "Noida",
                      "Greater Noida", "Ghaziabad", "Bareilly"],
    "Bihar": ["Patna", "Muzaffarpur"],
    "Jharkhand": ["Ranchi", "Jamshedpur", "Dhanbad"],
    "Madhya Pradesh": ["Bhopal", "Indore", "Gwalior", "Jabalpur"],
    "Chhattisgarh": ["Raipur", "Bilaspur"],
    "Punjab": ["Mohali", "Ludhiana", "Amritsar", "Jalandhar", "Patiala", "Bathinda"],
    "Haryana": ["Gurugram", "Faridabad", "Panchkula", "Karnal", "Sonipat", "Hisar", "Rohtak", "Ambala",
                "Kurukshetra"],
    "Uttarakhand": ["Dehradun", "Roorkee", "Haridwar", "Rishikesh"],
    "Himachal Pradesh": ["Shimla", "Mandi"],
    "Jammu and Kashmir": ["Jammu", "Srinagar"],
    "Kerala": ["Kochi", "Thiruvananthapuram", "Kozhikode", "Thrissur", "Kollam", "Kannur"],
    "Assam": ["Guwahati", "Silchar"],
    "Meghalaya": ["Shillong"],
    "Goa": ["Goa", "Panaji"],
    "Delhi": ["Delhi", "New Delhi"],
    "Chandigarh": ["Chandigarh"],
    "Puducherry": ["Puducherry"],
}
STATE_ALIASES = {
    "tamilnadu": "Tamil Nadu", "tn": "Tamil Nadu", "ap": "Andhra Pradesh", "up": "Uttar Pradesh",
    "mp": "Madhya Pradesh", "hp": "Himachal Pradesh", "j&k": "Jammu and Kashmir", "jk": "Jammu and Kashmir",
    "orissa": "Odisha", "pondicherry": "Puducherry", "ncr": "Delhi", "delhi ncr": "Delhi",
    "uttaranchal": "Uttarakhand", "maharastra": "Maharashtra", "karnatak": "Karnataka",
    "westbengal": "West Bengal", "telengana": "Telangana", "andhrapradesh": "Andhra Pradesh",
}
STATE_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(s) for s in list(STATES) + list(STATE_ALIASES)),
                             key=len, reverse=True)).replace(r"\ ", r"\s+") + r")\b", re.I)


def state_from(text: str) -> str:
    """The Indian state a request names, if any. '' when it names none."""
    match = STATE_RE.search(text or "")
    if not match:
        return ""
    found = " ".join(match.group(1).split()).casefold()
    for name in STATES:
        if name.casefold() == found:
            return name
    return STATE_ALIASES.get(found, "")


# Country words that mean a site is not an Indian institution.
FOREIGN_HOST = re.compile(
    r"\.(?:ac\.uk|edu\.au|ac\.nz|edu\.sg|edu\.my|ac\.za|edu\.pk|ac\.bd|ac\.lk|ac\.ae|edu\.ph|"
    r"ac\.jp|edu\.cn|ac\.kr|edu\.hk)$", re.I)

INSTITUTION_WORDS = re.compile(
    r"\b(?:College|University|Institute|Vidyapeeth|Vidyalaya|Mahavidyalaya|Academy|School of [A-Z][a-z]+|IIT|NIT|"
    r"IIIT|IISc|IISER|BITS|Polytechnic|Deemed)\b")
# kept for code shared with the hospital scraper
HOSPITAL_WORDS = INSTITUTION_WORDS

# "Best Engineering College in Mumbai", "Top 10 Colleges 2026": advertising copy
# and search-result headlines, never the name of a college.
MARKETING_NAME = re.compile(
    r"\b(?:best|top\s*\d*|no\.?\s*\d+|#\s*\d+|rank(?:ed|ing|ings)?|list\s+of|leading|finest|"
    r"cheapest|affordable|popular|famous|compare|fees?\s+structure|admission\s*20\d\d|"
    r"cut\s*off|counsell?ing|placement\s+(?:record|package)s?)\b", re.I)
# "Engineering College", "Institute of Technology": the kind of place, with
# nothing that says which one. Every real name carries something of its own.
GENERIC_NAME_WORDS = {"the", "of", "and", "in", "for", "a", "an", "college", "colleges", "institute",
                      "institutes", "university", "school", "academy", "polytechnic", "engineering",
                      "technology", "technological", "science", "sciences", "management", "studies",
                      "education", "arts", "commerce", "medical", "group", "campus"}


def is_marketing_name(name: str) -> bool:
    """True when the text reads like a search headline rather than a college name."""
    words = re.findall(r"[a-z]+", (name or "").casefold())
    if not words or all(w in GENERIC_NAME_WORDS for w in words):
        return True
    return bool(MARKETING_NAME.search(name or ""))

# --------------------------------------------------------------------------
# Labels used on faculty profile pages (lower-case, punctuation stripped) -> field
LABELS: dict[str, str] = {
    "name": "name", "faculty name": "name", "name of the faculty": "name", "name of faculty": "name",
    "staff name": "name", "name of the staff": "name",
    "designation": "designation", "position": "designation", "title": "designation", "role": "designation",
    "current designation": "designation", "present designation": "designation", "post": "designation",
    "department": "department", "dept": "department", "school": "department", "division": "department",
    "centre": "department", "center": "department", "discipline": "department", "branch": "department",
    "specialization": "specialty", "specialisation": "specialty", "area of specialization": "specialty",
    "area of specialisation": "specialty", "field of specialization": "specialty", "specialty": "specialty",
    "speciality": "specialty", "major": "specialty",
    "institute": "institution", "institution": "institution", "college": "institution", "university": "institution",
    "organisation": "institution", "organization": "institution", "campus": "institution",
    "city": "city",
    "experience": "experience_years", "total experience": "experience_years", "years of experience": "experience_years",
    "teaching experience": "experience_years", "experience in years": "experience_years",
    "qualification": "qualifications", "qualifications": "qualifications", "degree": "qualifications",
    "degrees": "qualifications", "educational qualification": "qualifications", "educational qualifications": "qualifications",
    "academic qualification": "qualifications", "academic qualifications": "qualifications",
    "highest qualification": "qualifications",
    "education": "education", "academic background": "education", "education details": "education",
    "educational background": "education",
    "research interests": "research_areas", "research interest": "research_areas", "areas of interest": "research_areas",
    "area of interest": "research_areas", "research areas": "research_areas", "research area": "research_areas",
    "areas of research": "research_areas", "research": "research_areas", "interests": "research_areas",
    "areas of expertise": "research_areas", "area of expertise": "research_areas", "expertise": "research_areas",
    "research focus": "research_areas", "fields of interest": "research_areas",
    "subjects taught": "subjects", "subjects": "subjects", "courses taught": "subjects", "courses": "subjects",
    "teaching": "subjects", "teaching interests": "subjects", "subjects handled": "subjects",
    "courses handled": "subjects",
    "awards": "awards", "awards & recognition": "awards", "awards and recognition": "awards", "achievements": "awards",
    "awards & achievements": "awards", "honours": "awards", "honors": "awards", "awards and honours": "awards",
    "memberships": "memberships", "professional memberships": "memberships", "membership": "memberships",
    "professional bodies": "memberships", "member of": "memberships", "professional affiliations": "memberships",
    "phone": "phone", "contact": "phone", "contact number": "phone", "telephone": "phone", "mobile": "phone",
    "tel": "phone", "mobile no": "phone", "mobile number": "phone", "phone no": "phone", "phone number": "phone",
    "contact no": "phone", "contact details": "phone", "extension": "contact_phone", "ext": "contact_phone",
    "office phone": "contact_phone", "office": "contact_phone", "landline": "contact_phone", "intercom": "contact_phone",
    "email": "email", "e-mail": "email", "email id": "email", "e mail": "email", "mail": "email",
    "email address": "email", "mail id": "email", "official email": "email", "e-mail id": "email",
    "google scholar": "google_scholar", "google scholar profile": "google_scholar", "orcid": "orcid",
    "orcid id": "orcid", "scopus": "scopus_id", "scopus id": "scopus_id", "scopus author id": "scopus_id",
    "researchgate": "researchgate", "linkedin": "linkedin", "vidwan": "vidwan", "vidwan id": "vidwan",
    "irins": "vidwan", "homepage": "homepage", "home page": "homepage", "personal website": "homepage",
    "website": "homepage",
    "about": "about", "profile": "about", "overview": "about", "biography": "about", "bio": "about",
    "summary": "about", "brief profile": "about", "professional summary": "about", "about me": "about",
}


def label_key(text: str) -> str:
    text = text.strip().lower().rstrip(":-–").strip()
    text = re.sub(r"^about\s+(?:dr\.?|prof\.?|mr\.?|ms\.?|mrs\.?)\b.*$", "about", text)
    text = re.sub(r"[^a-z&\- ]+", " ", text)
    return " ".join(text.split())


def label_field(text: str) -> str | None:
    key = label_key(text)
    if key in LABELS:
        return LABELS[key]
    if len(key) > 40:
        return None
    for k, v in LABELS.items():
        if len(k) > 4 and (key.startswith(k + " ") or key.endswith(" " + k)):
            return v
    return None


# --------------------------------------------------------------------------
# Student-facing offices and bodies. Order matters: first match wins.
OFFICE_TYPES: list[tuple[str, re.Pattern]] = [
    ("higher_studies", re.compile(
        r"higher[- _]?stud(?:y|ies)|study[- _]?abroad|foreign[- _]?(?:education|universit|studies)|overseas[- _]?education|"
        r"\bgre\b|toefl|ielts|ms[- _]?abroad|pg[- _]?abroad", re.I)),
    ("placement", re.compile(
        r"training[- _]?(?:and|&|-)?[- _]?placement|\bt[- _]?(?:&|and|n)?[- _]?p\b|\btpo\b|placement|career[- _]?"
        r"(?:development|guidance|services|cell|centre|center)|corporate[- _]?relations|\bcdc\b|\bcrc\b", re.I)),
    ("international", re.compile(
        r"international[- _]?(?:relations|affairs|office|students?|cooperation)|\biro\b|global[- _]?(?:engagement|relations)|"
        r"outreach[- _]?international", re.I)),
    ("alumni", re.compile(r"alumni|alumnae|old[- _]?students", re.I)),
    ("student_chapter", re.compile(
        r"student[- _]?(?:chapter|branch|clubs?|societ(?:y|ies)|activit(?:y|ies)|bodies|council|forum)|"
        r"\b(?:ieee|acm|csi|iete|iste|sae|asme|iei|isa|gdsc|gdg)\b|technical[- _]?clubs?|clubs?[- _]?(?:and|&)", re.I)),
    ("student_affairs", re.compile(
        r"student[- _]?(?:affairs|welfare|services|support|grievance)|dean[- _]?(?:of[- _]?)?student|\bdsw\b|"
        r"counsell?ing[- _]?(?:cell|centre|center)|mentoring", re.I)),
]

# A line about an individual student: its phones/emails are not taken.
STUDENT_MARKERS = re.compile(
    r"\bstudent[- ](?:coordinator|co-?ordinator|representative|member|secretary|volunteer|head|lead|"
    r"president|chair|placement|placement coordinator)s?\b|\bstudents?\s*(?:co-?ordinators?|reps?)\b|"
    r"\b(?:roll|reg(?:istration)?|enrol(?:l)?ment|htno|usn|prn)\.?\s*(?:no|number|#)?\b\s*[:.\-]?\s*[A-Z0-9]{4,}|"
    r"\b(?:I|II|III|IV|1st|2nd|3rd|4th|final|pre-?final)\s*(?:year|yr|sem(?:ester)?)\b|"
    r"\b(?:B\.?\s?Tech|B\.?\s?E|M\.?\s?Tech|MCA|MBA|B\.?\s?Sc|BCA)\b\s*(?:[-–(,]\s*)?(?:\d{4}|I{1,3}V?\b|[1-4](?:st|nd|rd|th)?\s*(?:year|yr|sem))|"
    r"\bbatch\s*(?:of\s*)?(?:19|20)\d{2}\b|\bclass of (?:19|20)\d{2}\b|"
    r"\b(?:president|vice[- ]president|general secretary|joint secretary|secretary|treasurer|"
    r"(?:vice[- ])?chair(?:person)?|core (?:team|committee) member|volunteer|web master|webmaster|"
    r"student branch counsellor? \(student\))\b",
    re.I,
)
# Words that make such a line a staff line after all ('Faculty Advisor', 'Professor & Branch Counsellor').
STAFF_IN_LINE = re.compile(r"\b(?:faculty|professor|prof\b|dr\.|lecturer|hod|dean|officer|staff|counsell?or|"
                           r"advisor|adviser|mentor|in-?charge)\b", re.I)
# Page sections and pages that list people who are not the college's staff:
# alumni, toppers and placed students, testimonials, guest speakers, recruiters, governing-body members.
NOT_STAFF_SECTION = re.compile(
    r"alumn|old[- _]?students|achiever|success[- _]?stor|hall[- _]?of[- _]?fame|notable|distinguished[- _]?(?:alumni|guests?)|"
    r"testimonial|what[- _]?(?:our|students|parents|alumni)[- _]?say|student[- _]?(?:speak|voice|corner|achievements?)|"
    r"placed[- _]?students|placement[- _]?(?:record|highlights?|stories)|our[- _]?stars|toppers?|rank[- _]?holders?|"
    r"gold[- _]?medal|university[- _]?ranks?|recruiters?|guest[- _]?(?:lectures?|speakers?)|expert[- _]?talks?|"
    r"chief[- _]?guests?|resource[- _]?persons?|governing[- _]?(?:body|council)|board[- _]?of[- _]?(?:governors|management|trustees)|"
    r"trustees?|management[- _]?committee|founders?\b|news|events?|blog",
    re.I)
# Words on a card that mark a graduate, not a staff member: 'Alumnus', 'Batch 2012', 'Software Engineer at X'.
# Degree years are not used: faculty cards often show them too ('Ph.D (2012)').
ALUMNI_CARD = re.compile(
    r"\balumn(?:us|a|i|ae)\b|\bbatch\b|\bclass of\b|passed out|pass[- ]?out|"
    r"\b(?:CEO|CTO|COO|CFO|Co-?founder|Founder|Software Engineer|SDE|Engineering Manager|Product Manager|"
    r"Vice President|VP|Managing Director|Senior Engineer)\b.*\b(?:at|@|,)\s*[A-Z]",
    re.I)
# A section heading that lists the college's own teaching staff.
STAFF_SECTION = re.compile(r"faculty|faculties|teaching staff|our team|professors|people|staff|hod|heads? of",
                           re.I)

# Published counts of students going for higher studies.
HIGHER_STUDIES_LINE = re.compile(
    r"(?:higher\s+stud(?:y|ies)|abroad|foreign\s+universit|\bms\s+in\b|\bgre\b|toefl|ielts|admits?|"
    r"universities in (?:the )?(?:usa|us|uk|germany|canada|australia))", re.I)

# --------------------------------------------------------------------------
# URL hints
PROFILE_URL_HINTS = re.compile(
    r"/(?:faculty|faculties|people|staff|profile|profiles|faculty-profile|facultyprofile|faculty-details|"
    r"facultydetails|faculty_profile|teaching-staff|team|member|members|employee|emp|person|directory)/[^/?#]+/?$"
    r"|/(?:faculty|staff|people)[-_]?(?:profile|details|info)[^/]*[?&](?:id|emp|empid|fid|staffid|uid)=|"
    r"/(?:dr|prof|mr|ms|mrs)[-_.][a-z]|[?&](?:fid|facultyid|empid|staffid)=\d",
    re.I,
)
LISTING_URL_HINTS = re.compile(
    r"(?:faculty|faculties|people|staff|teaching|our-team|team|directory|department|departments|dept|school|"
    r"academics?|programmes?|programs?|branch|branches|hod|who-?s-?who|members)",
    re.I,
)
OFFICE_URL_HINTS = re.compile("|".join(p.pattern for _, p in OFFICE_TYPES), re.I)
FOCUS_URL_HINTS = re.compile(r"faculty|faculties|people|staff|department|dept|teaching", re.I)
SKIP_URL = re.compile(
    r"\.(?:pdf|jpe?g|png|gif|svg|webp|ico|css|js|mp4|mp3|zip|rar|docx?|xlsx?|pptx?)(?:$|\?)|"
    r"/(?:blogs?|news|newsletters?|newsroom|media|press|press-releases?|articles?|events?|gallery|photo-?gallery|"
    r"videos?|careers?|jobs|recruitment|vacanc(?:y|ies)|tenders?|login|signin|sign-in|register|registration|"
    r"apply|apply-now|admissions?-?(?:open|form)|online-?payment|fee-?payment|pay|cart|checkout|"
    r"search|tag|tags|author|feed|wp-json|wp-admin|wp-content|cdn-cgi|share|print|amp|"
    r"circulars?|notices?|notifications?|downloads?|results?|exam(?:ination)?s?|time-?tables?|syllabus|"
    r"question-?papers?|calendar|hi|mr|bn|ta|te|kn|gu|ml|nirf|naac|nba|aicte|iqac|mandatory-disclosure|rti|"
    r"anti-?ragging|grievance-?form|feedback|hostel|transport|canteen|sports)(?:/|$)|"
    r"^(?:mailto|tel|javascript|whatsapp):",
    re.I,
)
LINK_TEXT_HINTS = re.compile(
    r"faculty|faculties|people|staff|teaching|department|dept|our team|who'?s who|members|view all|see all|"
    r"placement|training|career|higher stud|study abroad|alumni|student (?:chapter|branch|clubs?|activities)|"
    r"clubs|ieee|acm|international", re.I)
PAGINATION_RE = re.compile(r"(?:[?&](?:page|p|pg|paged|start|offset)=\d+|/page/\d+/?$)", re.I)
