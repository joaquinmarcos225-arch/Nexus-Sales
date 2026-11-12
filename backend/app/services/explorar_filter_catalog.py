"""Catálogo de filtros Explorar = valores que Prospeo Search Person acepta.

Fuente única para backend (build_filters / Posibles clientes) y frontend (chips).
Verificado contra enum Prospeo + Search Person (MX) 2026-11-04.
No agregar un valor sin probar Search Person (INVALID_FILTERS o 0 hits).
"""

from __future__ import annotations

from typing import Any

# (valor_api, etiqueta_es)
# Labels viejos que Prospeo rechaza: Retail / Wholesale / Manufacturing / Education
# → usar General Retail / General Wholesale / General Manufacturing / Higher Education, etc.
INDUSTRY_CHOICES: tuple[tuple[str, str], ...] = (
    # Primero = chips visibles sin «+ más» (SaaS + locales + Uber Envios).
    ("Software Development", "Software"),
    ("Online and Mail Order Retail", "Ecommerce"),
    ("Restaurants", "Restaurantes"),
    ("Retail Pharmacies", "Farmacias"),
    ("General Retail", "Retail"),
    ("IT Services and IT Consulting", "Servicios IT"),
    ("Food and Beverage Services", "Gastronomía"),
    ("Consumer Goods", "Consumo masivo"),
    ("Marketing Services", "Marketing"),
    ("Transportation, Logistics, Supply Chain and Storage", "Logística y transporte"),
    ("Technology, Information and Internet", "Tecnología e internet"),
    ("Internet Marketplace Platforms", "Marketplaces"),
    ("Retail Apparel and Fashion", "Moda / retail"),
    ("Retail Groceries", "Supermercados"),
    ("Consumer Services", "Servicios al consumidor"),
    ("Mobile Food Services", "Food trucks / comida móvil"),
    ("Food and Beverage Retail", "Retail comida / bebida"),
    ("Food and Beverage Manufacturing", "Alimentos (fábrica)"),
    ("Wholesale Food and Beverage", "Mayorista alimentos"),
    ("Breweries", "Cervecerías"),
    ("Retail Health and Personal Care Products", "Cuidado personal (retail)"),
    ("Wholesale Drugs and Sundries", "Mayorista farma"),
    ("Pharmaceutical Manufacturing", "Farmacéutica"),
    ("Hospitals and Health Care", "Salud"),
    ("Medical Practices", "Consultorios médicos"),
    ("Dentists", "Odontología"),
    ("Wellness and Fitness Services", "Wellness / fitness"),
    ("Cosmetics", "Cosmética"),
    ("Personal Care Product Manufacturing", "Cuidado personal (fábrica)"),
    ("Vehicle Repair and Maintenance", "Talleres / autos"),
    ("Pet Services", "Mascotas"),
    ("Facilities Services", "Facilities"),
    ("Events Services", "Eventos"),
    ("Design Services", "Diseño"),
    ("Professional Services", "Servicios profesionales"),
    ("Accommodation Services", "Alojamiento"),
    ("Hospitality", "Hotelería y turismo"),
    ("Financial Services", "Servicios financieros"),
    ("Banking", "Banca"),
    ("Insurance", "Seguros"),
    ("Real Estate", "Inmobiliaria"),
    ("Construction", "Construcción"),
    ("Advertising Services", "Publicidad"),
    ("Business Consulting and Services", "Consultoría"),
    ("Staffing and Recruiting", "RRHH y selección"),
    ("Human Resources Services", "Servicios RRHH"),
    ("Legal Services", "Legales"),
    ("Accounting", "Contabilidad"),
    ("Telecommunications", "Telecomunicaciones"),
    ("Motor Vehicle Manufacturing", "Automotriz"),
    ("General Manufacturing", "Manufactura"),
    ("General Wholesale", "Mayorista"),
    # Educación: «Education» solo → INVALID; estos labels sí pasan.
    ("E-Learning Providers", "E-learning"),
    ("Higher Education", "Educación superior"),
    ("Education Administration Programs", "Educación (admin)"),
    ("Primary and Secondary Education", "Educación primaria/secundaria"),
)

# Valores que alguna vez estuvieron en UI / ICP libre y Prospeo rechaza.
KNOWN_INVALID_INDUSTRIES = frozenset(
    {
        "Manufacturing",
        "Retail",
        "Wholesale",
        "Education",
        "Customer Service",  # también inválido como department
        # Alias viejos / LinkedIn legacy → Search Person INVALID_FILTERS
        "Computer Software",
        "Internet",
        "Information Technology",
        "Information Technology and Services",
    }
)

SENIORITY_CHOICES: tuple[tuple[str, str], ...] = (
    ("Founder/Owner", "Dueño / Fundador"),
    ("C-Suite", "C-Level"),
    ("Partner", "Partner"),
    ("Vice President", "VP"),
    ("Head", "Head"),
    ("Director", "Director"),
    ("Manager", "Gerente"),
    ("Senior", "Senior"),
    ("Entry", "Junior"),
)

# «Customer Service» da INVALID_FILTERS — no listar.
DEPARTMENT_CHOICES: tuple[tuple[str, str], ...] = (
    ("C-Suite", "Dirección"),
    ("Sales", "Ventas"),
    ("Marketing", "Marketing"),
    ("Finance", "Finanzas"),
    ("Human Resources", "RRHH"),
    ("Operations", "Operaciones"),
    ("Information Technology", "Sistemas / IT"),
    ("Engineering & Technical", "Ingeniería"),
    ("Product", "Producto"),
    ("Design", "Diseño"),
    ("Legal", "Legales"),
)

HEADCOUNT_CHOICES: tuple[tuple[str, str], ...] = (
    ("1-10", "1–10"),
    ("11-20", "11–20"),
    ("21-50", "21–50"),
    ("51-100", "51–100"),
    ("101-200", "101–200"),
    ("201-500", "201–500"),
    ("501-1000", "501–1.000"),
    ("1001-2000", "1.001–2.000"),
    ("2001-5000", "2.001–5.000"),
    ("5001-10000", "5.001–10.000"),
    ("10000+", "+10.000"),
)

INDUSTRY_OPTIONS = tuple(v for v, _ in INDUSTRY_CHOICES)
SENIORITY_OPTIONS = tuple(v for v, _ in SENIORITY_CHOICES)
DEPARTMENT_OPTIONS = tuple(v for v, _ in DEPARTMENT_CHOICES)
HEADCOUNT_OPTIONS = tuple(v for v, _ in HEADCOUNT_CHOICES)

CATALOG_VERSION = "2026-11-04-prospeo"


def _pairs(choices: tuple[tuple[str, str], ...]) -> list[dict[str, str]]:
    return [{"value": value, "label": label} for value, label in choices]


def filter_catalog() -> dict[str, Any]:
    """Payload para GET /explorar/filter-catalog (UI + clients)."""
    return {
        "version": CATALOG_VERSION,
        "industries": _pairs(INDUSTRY_CHOICES),
        "seniorities": _pairs(SENIORITY_CHOICES),
        "departments": _pairs(DEPARTMENT_CHOICES),
        "headcounts": _pairs(HEADCOUNT_CHOICES),
    }


def sanitize_industry_values(values: list[str] | None, *, limit: int = 6) -> list[str]:
    allowed = set(INDUSTRY_OPTIONS)
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        v = str(raw or "").strip()
        if not v or v in KNOWN_INVALID_INDUSTRIES or v not in allowed:
            continue
        key = v.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
        if len(out) >= limit:
            break
    return out
