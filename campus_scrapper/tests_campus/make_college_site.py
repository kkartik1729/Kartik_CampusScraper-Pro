"""Builds the fictional college test site under tests_campus/site/. All people, colleges and numbers are invented.

Each page imitates a layout seen on Indian college websites: faculty card grids with
Dr./Prof./Mr./Ms. names, faculty tables without honorifics, label/value profiles,
dt/dd profiles, JSON-LD Person with sameAs links, non-teaching staff tables, a
Training & Placement page with a student-coordinator table, a Higher Studies cell,
an IEEE student branch with student office-bearers, and a noisy header/footer.
"""
from pathlib import Path

SITE = Path(__file__).parent / "site"
COLLEGE = "Sunrise Institute of Technology, Hyderabad"

HEAD = """<header class="site-header"><div class="top-bar">Call: 040-2345 6789 | info@sunrise-it.ac.in</div>
<nav class="navbar"><a href="/">Home</a> <a href="/about">About</a> <a href="/departments">Departments</a>
<a href="/admissions-open">Admissions</a> <a href="/news/convocation-2026">News</a> <a href="/nirf">NIRF</a>
<a href="/training-and-placement">Training &amp; Placement</a> <a href="/higher-studies-cell">Higher Studies</a>
<a href="/student-chapters/ieee">IEEE Student Branch</a> <a href="/alumni">Alumni</a></nav></header>"""
FOOT = """<footer class="site-footer"><p>Sunrise Institute of Technology, Gandipet, Hyderabad 500075</p>
<p>Phone: 040-2345 6789 | Email: info@sunrise-it.ac.in | principal@sunrise-it.ac.in</p></footer>"""


def page(title: str, body: str, head_extra: str = "") -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<meta property="og:site_name" content="{COLLEGE}">{head_extra}</head>
<body>{HEAD}<main>{body}</main>{FOOT}</body></html>"""


FILES: dict[str, str] = {}

FILES["index.html"] = page("Sunrise Institute of Technology | Autonomous | Hyderabad", """
<section class="hero"><h1>Shaping engineers since 1998</h1><p>NAAC A++ accredited. Affiliated to JNTUH.</p></section>
<section><h2>Departments</h2><ul><li><a href="/departments/cse">Computer Science and Engineering</a></li>
<li><a href="/departments/mech">Mechanical Engineering</a></li></ul>
<a href="/faculty">Our Faculty</a> <a href="/careers/assistant-professor">We are hiring</a></section>
<section class="home-block"><h2>Prestigious Alumni</h2><p>We teach values</p>
<div class="owl-carousel">
<div class="item"><img src="/img/a1.jpg" alt="Dr. Sanjay Patil"><h4>Dr. Sanjay Patil</h4><p>Director, Infotech Solutions</p></div>
<div class="item"><img src="/img/a2.jpg" alt="Mr. Nikhil Deshmukh"><h4>Mr. Nikhil Deshmukh</h4><p>Scientist, ISRO</p></div>
<div class="item"><img src="/img/a3.jpg" alt="Dr. Meera Joshi"><h4>Dr. Meera Joshi</h4><p>Assistant Professor, IIT Bombay</p></div>
</div></section>
<section class="home-block"><h2>Faculty Members</h2>
<div class="owl-carousel">
<div class="item"><img src="/img/f1.jpg" alt="Dr. Anitha Rao"><h4><a href="/faculty/dr-anitha-rao">Dr. Anitha Rao</a></h4><p>Professor &amp; Head, CSE</p></div>
<div class="item"><img src="/img/f2.jpg" alt="Prof. K. Srinivas"><h4><a href="/faculty/prof-k-srinivas">Prof. K. Srinivas</a></h4><p>Associate Professor, CSE</p></div>
</div></section>
<div class="testimonial-slider"><div class="t"><h4>Ms. Pooja Kale</h4><p>Software Engineer at Google</p><p>"The faculty here shaped my career."</p></div>
<div class="t"><h4>Mr. Rohan Mehta</h4><p>B.E. CSE, Batch 2015, Senior Engineer, Amazon</p></div></div>
<a href="/notable-alumni">Notable Alumni</a>""")

FILES["robots.txt"] = """User-agent: *
Disallow: /admin/
Disallow: *?sort=*
Sitemap: /sitemap.xml
"""

FILES["sitemap.xml"] = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>{BASE}/</loc></url>
<url><loc>{BASE}/faculty/dr-anitha-rao</loc></url>
<url><loc>{BASE}/news/convocation-2026</loc></url>
</urlset>"""

FILES["departments.html"] = page("Departments | Sunrise Institute of Technology", """
<h1>Departments</h1><ul>
<li><a href="/departments/cse">Computer Science and Engineering</a> - B.Tech, M.Tech, PhD</li>
<li><a href="/departments/mech">Mechanical Engineering</a> - B.Tech, M.Tech</li></ul>""")

FILES["faculty.html"] = page("Faculty | Sunrise Institute of Technology", """
<h1>Our Faculty</h1><p>Choose a department.</p>
<ul><li><a href="/departments/cse/faculty">CSE Faculty</a></li><li><a href="/departments/mech/faculty">Mechanical Faculty</a></li></ul>""")

FILES["departments/cse.html"] = page("Department of Computer Science and Engineering | Sunrise", """
<nav aria-label="breadcrumb" class="breadcrumb"><ol><li><a href="/">Home</a></li><li>Departments</li><li>Computer Science and Engineering</li></ol></nav>
<h1>Department of Computer Science and Engineering</h1><p>The department offers B.Tech, M.Tech and PhD programmes.</p>
<p><a href="/departments/cse/faculty">Faculty</a> | <a href="/departments/cse/labs">Laboratories</a></p>
<p>HOD office: hodcse@sunrise-it.ac.in, Ext. 2201</p>""")

CARD = """<div class="faculty-card"><img src="/img/{slug}.jpg" alt="{name}">
<h4 class="fc-name"><a href="/faculty/{slug}">{name}</a></h4><p class="fc-desig">{desig}</p>
<p>{quals}</p><p><a href="mailto:{email}">{email}</a></p><a class="btn" href="/faculty/{slug}">View Profile</a></div>"""


def cards(items):
    return "\n".join(CARD.format(**i) for i in items)


FILES["departments/cse/faculty.html"] = page("CSE Faculty | Sunrise Institute of Technology", f"""
<nav aria-label="breadcrumb" class="breadcrumb"><ol><li><a href="/">Home</a></li><li><a href="/departments/cse">Computer Science and Engineering</a></li><li>Faculty</li></ol></nav>
<h1>Faculty - Computer Science and Engineering</h1>
<div class="faculty-grid">{cards([
    dict(slug="dr-anitha-rao", name="Dr. Anitha Rao", desig="Professor &amp; Head", quals="Ph.D, M.Tech (CSE)", email="hodcse@sunrise-it.ac.in"),
    dict(slug="prof-k-srinivas", name="Prof. K. Srinivas", desig="Associate Professor", quals="M.Tech, (Ph.D)", email="srinivas.k@sunrise-it.ac.in"),
    dict(slug="ms-priya-sharma", name="Ms. Priya Sharma", desig="Assistant Professor", quals="M.Tech (AI &amp; ML)", email="priya.sharma@sunrise-it.ac.in"),
    dict(slug="mr-rajesh-kumar", name="Mr. Rajesh Kumar", desig="Assistant Professor", quals="M.E. (CSE), B.E.", email="rajesh.kumar@sunrise-it.ac.in"),
])}</div>
<ul class="pagination"><li class="active"><a href="/departments/cse/faculty">1</a></li><li><a href="/departments/cse/faculty?page=2" rel="next">Next ›</a></li></ul>""")

FILES["departments/cse/faculty__page=2.html"] = page("CSE Faculty - Page 2 | Sunrise", f"""
<h1>Faculty - Computer Science and Engineering</h1>
<div class="faculty-grid">{cards([
    dict(slug="dr-farah-siddiqui", name="Dr. Farah Siddiqui", desig="Associate Professor (Sr. Grade)", quals="Ph.D (CSE), IIT Madras", email="farah.s@sunrise-it.ac.in"),
    dict(slug="mrs-lakshmi-devi", name="Mrs. Lakshmi Devi", desig="Assistant Professor", quals="MCA, M.Tech", email="lakshmi.devi@sunrise-it.ac.in"),
])}</div>""")

# Mechanical: a table without honorifics, and a non-teaching staff table.
FILES["departments/mech/faculty.html"] = page("Mechanical Faculty | Sunrise Institute of Technology", """
<h1>Department of Mechanical Engineering - Faculty</h1>
<h3>Teaching Staff</h3>
<table class="table"><tr><th>S.No</th><th>Name of the Faculty</th><th>Designation</th><th>Qualification</th><th>Email</th></tr>
<tr><td>1</td><td>Venkata Ramana Murthy</td><td>Professor</td><td>Ph.D, M.Tech (Thermal)</td><td>vr.murthy@sunrise-it.ac.in</td></tr>
<tr><td>2</td><td>Sneha Kulkarni</td><td>Assistant Professor</td><td>M.Tech (CAD/CAM)</td><td>sneha.k@sunrise-it.ac.in</td></tr>
</table>
<h3>Non-Teaching Staff</h3>
<table class="table"><tr><th>S.No</th><th>Name</th><th>Designation</th><th>Qualification</th></tr>
<tr><td>1</td><td>Mr. Ramesh Babu</td><td>Lab Assistant</td><td>Diploma</td></tr>
<tr><td>2</td><td>Mr. Suresh Naik</td><td>Technician</td><td>ITI</td></tr>
</table>""")

FILES["faculty/dr-anitha-rao.html"] = page("Dr. Anitha Rao | CSE | Sunrise Institute of Technology", """
<nav aria-label="breadcrumb" class="breadcrumb"><ol><li><a href="/">Home</a></li><li><a href="/departments/cse">Computer Science and Engineering</a></li><li>Dr. Anitha Rao</li></ol></nav>
<div class="profile"><img src="/img/dr-anitha-rao.jpg" alt="Dr. Anitha Rao"><h1>Dr. Anitha Rao</h1>
<p class="desig">Professor &amp; Head, Department of Computer Science and Engineering</p>
<div class="info"><div class="label">Qualification</div><div class="value">Ph.D (Computer Science), JNTU Hyderabad; M.Tech (CSE), Osmania University; B.Tech</div>
<div class="label">Experience</div><div class="value">22 Years</div>
<div class="label">Email</div><div class="value">anitha.rao@sunrise-it.ac.in</div>
<div class="label">Phone</div><div class="value">Mobile: +91 98480 11111</div></div>
<h3>Research Interests</h3><ul><li>Machine Learning</li><li>Natural Language Processing</li><li>Information Retrieval</li></ul>
<h3>Subjects Taught</h3><ul><li>Data Structures</li><li>Compiler Design</li></ul>
<h3>Professional Memberships</h3><ul><li>Senior Member, IEEE</li><li>Life Member, CSI</li></ul>
<p><a href="https://scholar.google.co.in/citations?user=AnItHa01xyz">Google Scholar</a> |
<a href="https://orcid.org/0000-0002-1825-0097">ORCID</a> | <a href="https://vidwan.inflibnet.ac.in/profile/111111">Vidwan</a></p>
<p>Dr. Anitha Rao has guided 8 PhD scholars and published over 60 papers in machine learning and text mining. She leads the department's research in language technologies.</p>
</div>""")

FILES["faculty/prof-k-srinivas.html"] = page("Prof. K. Srinivas - Faculty Profile", """
<h1>Prof. K. Srinivas</h1><p>Associate Professor, Computer Science and Engineering</p>
<p>Qualifications: M.Tech (Software Engineering), Ph.D (pursuing)</p>
<p>Research Areas: Cloud Computing, Distributed Systems</p>""", head_extra="""
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Person","name":"Prof. K. Srinivas",
"jobTitle":"Associate Professor","worksFor":{"@type":"CollegeOrUniversity","name":"Sunrise Institute of Technology"},
"email":"srinivas.k@sunrise-it.ac.in","knowsAbout":["Cloud Computing","Distributed Systems"],
"sameAs":["https://www.linkedin.com/in/ksrinivas-example","https://orcid.org/0000-0001-5109-3700"]}</script>""")

FILES["faculty/ms-priya-sharma.html"] = page("Ms. Priya Sharma | Sunrise", """
<h1>Ms. Priya Sharma</h1>
<dl><dt>Designation</dt><dd>Assistant Professor</dd><dt>Department</dt><dd>Computer Science and Engineering</dd>
<dt>Qualification</dt><dd>M.Tech (AI &amp; ML), B.Tech (IT)</dd><dt>Experience</dt><dd>6 years</dd>
<dt>Area of Interest</dt><dd>Deep Learning, Computer Vision</dd><dt>E-mail</dt><dd>priya.sharma@sunrise-it.ac.in</dd>
<dt>Scopus ID</dt><dd>57212345678</dd></dl>""")

FILES["faculty/mr-rajesh-kumar.html"] = page("Mr. Rajesh Kumar | Sunrise", """
<h1>Mr. Rajesh Kumar</h1><p>Assistant Professor, Department of CSE</p><p>M.E. (CSE), B.E.</p>
<p>Email: rajesh.kumar@sunrise-it.ac.in</p>""")

FILES["faculty/dr-farah-siddiqui.html"] = page("Dr. Farah Siddiqui | Sunrise", """
<h1>Dr. Farah Siddiqui</h1><p>Associate Professor (Sr. Grade), Computer Science and Engineering</p>
<p>Ph.D (CSE), IIT Madras; M.Tech (CSE)</p><p>Research Interests: Network Security, Blockchain</p>
<p>Email: farah.s@sunrise-it.ac.in</p>""")

# ---- Student-facing offices --------------------------------------------------
FILES["training-and-placement.html"] = page("Training & Placement Cell | Sunrise Institute of Technology", """
<h1>Training &amp; Placement Cell</h1>
<p>The cell trains students for campus recruitment and supports students who choose higher studies abroad.</p>
<h3>Contact</h3>
<p>Dr. Mohan Krishna, Training and Placement Officer</p>
<p>Email: tpo@sunrise-it.ac.in | Office: 040-2345 6800</p>
<p>Mr. Arvind Rao, Assistant Placement Officer, Mobile: +91 98481 22222</p>
<h3>Placement Statistics 2024-25</h3>
<ul><li>412 students placed in 96 companies</li><li>42 students admitted to universities in the USA for MS (2024-25)</li>
<li>11 students joined MS programmes in Germany and Canada</li></ul>
<h3>Student Placement Coordinators</h3>
<table><tr><th>Name</th><th>Branch</th><th>Year</th><th>Mobile</th><th>Email</th></tr>
<tr><td>Rahul Verma</td><td>CSE</td><td>IV Year</td><td>+91 90000 33333</td><td>rahul.v21@student.sunrise-it.ac.in</td></tr>
<tr><td>Sravani Reddy</td><td>ECE</td><td>IV Year</td><td>+91 90000 44444</td><td>sravani.r21@student.sunrise-it.ac.in</td></tr>
</table>""")

FILES["higher-studies-cell.html"] = page("Higher Studies Cell | Study Abroad Guidance", """
<h1>Higher Studies Cell</h1>
<p>Guidance for GRE, TOEFL and IELTS, statement of purpose reviews, and university shortlisting.</p>
<p>Faculty Coordinator: Dr. Farah Siddiqui, Associate Professor, CSE - higherstudies@sunrise-it.ac.in</p>
<p>Student volunteer: Kiran Teja (III Year CSE), 9876500001</p>
<p>In 2025, 58 students received admits from universities in the USA, UK and Germany.</p>""")

FILES["student-chapters/ieee.html"] = page("IEEE Student Branch | Sunrise Institute of Technology", """
<h1>IEEE Student Branch</h1>
<p>Branch Counsellor: Prof. K. Srinivas (Faculty Advisor), ieee@sunrise-it.ac.in</p>
<h3>Office Bearers 2025-26</h3>
<ul><li>Chair: Naveen Kumar, IV Year ECE, 9000055555</li>
<li>Secretary: Divya Menon, III Year CSE, divya.m22@student.sunrise-it.ac.in</li>
<li>Treasurer: Arjun Patel, III Year EEE</li></ul>
<p>Contact the branch: 7000066666</p>""")

FILES["alumni.html"] = page("Alumni Association | Sunrise", """
<h1>Alumni Association</h1><p>Stay connected with 18,000 alumni across 30 countries.</p>
<p>Alumni Relations Officer: Mrs. Geetha Rani - alumni@sunrise-it.ac.in, 040-2345 6900</p>""")

FILES["notable-alumni.html"] = page("Notable Alumni | Sunrise Institute of Technology", """
<h1>Notable Alumni</h1><div class="list">
<div class="c"><h4>Dr. Vivek Kulkarni</h4><p>Professor, University of Michigan</p><p>Ph.D, M.Tech</p></div>
<div class="c"><h4>Dr. Asha Bhide</h4><p>Associate Professor, NIT Nagpur</p><p>Ph.D</p></div>
<div class="c"><h4>Mr. Tushar Gawande</h4><p>Founder, CloudNest</p></div></div>""")

# Noise and traps
FILES["news/convocation-2026.html"] = page("Convocation 2026", "<h1>Convocation 2026</h1><p>Dr. Chief Guest addressed graduates.</p>")
FILES["admin/staff-salaries.html"] = page("Private", "<h1>Dr. Secret Person</h1><p>Professor</p>")


def build() -> None:
    for name, html in FILES.items():
        path = SITE / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    build()
