"""Diagnóstico search-person Prospeo por empresa."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProspeoPersonDiscard:
    person_name: str | None
    reason: str
    stage: str
    email_domain: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "person_name": self.person_name,
            "reason": self.reason,
            "stage": self.stage,
            "email_domain": self.email_domain,
        }


@dataclass
class ProspeoSearchRequestLog:
    request_type: str
    executed: bool
    results_count: int = 0
    error: str | None = None
    error_code: str | None = None
    status_code: int | None = None
    filter_summary: str = ""
    search_outcome: str | None = None
    response_preview: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "request_type": self.request_type,
            "executed": self.executed,
            "results_count": self.results_count,
            "error": self.error,
            "error_code": self.error_code,
            "status_code": self.status_code,
            "filter_summary": self.filter_summary,
            "search_outcome": self.search_outcome,
        }
        if self.response_preview:
            out["response_preview"] = self.response_preview
        return out


@dataclass
class ProspeoCompanySearchDiagnostic:
    company_name: str
    domain_sent: str
    request_executed: bool = False
    requests: list[ProspeoSearchRequestLog] = field(default_factory=list)
    prospeo_results: int = 0
    after_dedupe: int = 0
    valid_results: int = 0
    discarded_count: int = 0
    discards: list[ProspeoPersonDiscard] = field(default_factory=list)
    api_error: str | None = None
    error_code: str | None = None
    search_outcome: str | None = None
    search_blocked: bool = False
    status_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        from app.services.lead_sourcing.prospeo_api_health import (
            is_http_success_error_code,
            is_search_blocked_outcome,
            outcome_discard_reason,
            outcome_status_message,
        )

        from app.services.lead_sourcing.prospeo_api_health import SEARCH_OUTCOME_NO_RESULTS

        summary = self._discard_summary()
        error_code = self.error_code
        search_outcome = self.search_outcome
        api_error = self.api_error
        search_blocked = self.search_blocked
        if is_http_success_error_code(error_code):
            error_code = None
            api_error = None
            search_blocked = False
            if is_search_blocked_outcome(search_outcome):
                search_outcome = SEARCH_OUTCOME_NO_RESULTS
        blocked = search_blocked or is_search_blocked_outcome(search_outcome)
        status = self.status_message or self._default_status()
        if blocked and not self.status_message:
            status = outcome_status_message(search_outcome or "", error_code=error_code)
        return {
            "company_name": self.company_name,
            "domain": self.domain_sent,
            "domain_sent": self.domain_sent,
            "request_executed": self.request_executed,
            "requests": [r.to_dict() for r in self.requests],
            "prospeo_results": self.prospeo_results,
            "after_dedupe": self.after_dedupe,
            "valid_results": self.valid_results,
            "discarded_count": self.discarded_count,
            "discard_reason": summary,
            "discard_reasons_summary": summary,
            "person_discards": [d.to_dict() for d in self.discards],
            "api_error": api_error,
            "error_code": error_code,
            "search_outcome": search_outcome,
            "search_blocked": blocked,
            "status_message": status,
        }

    def _discard_summary(self) -> str:
        from app.services.lead_sourcing.prospeo_api_health import (
            SEARCH_OUTCOME_NO_RESULTS,
            is_search_blocked_outcome,
            outcome_discard_reason,
        )

        if is_search_blocked_outcome(self.search_outcome):
            return outcome_discard_reason(
                self.search_outcome or "",
                error_code=self.error_code,
                detail=self.api_error,
            )
        if self.api_error and not self.search_outcome:
            return self.api_error
        if self.prospeo_results == 0 and self.search_outcome == SEARCH_OUTCOME_NO_RESULTS:
            return outcome_discard_reason(SEARCH_OUTCOME_NO_RESULTS)
        if self.prospeo_results == 0 and self.request_executed:
            return outcome_discard_reason(SEARCH_OUTCOME_NO_RESULTS)
        if self.valid_results == 0 and self.discards:
            reasons = {}
            for d in self.discards:
                reasons[d.reason] = reasons.get(d.reason, 0) + 1
            top = sorted(reasons.items(), key=lambda x: -x[1])[:3]
            return "; ".join(f"{r} ({n})" for r, n in top)
        if self.discarded_count:
            return f"{self.discarded_count} descartados, {self.valid_results} válidos"
        return "—"

    def _default_status(self) -> str:
        from app.services.lead_sourcing.prospeo_api_health import (
            SEARCH_OUTCOME_NO_RESULTS,
            is_search_blocked_outcome,
            outcome_status_message,
        )

        if is_search_blocked_outcome(self.search_outcome):
            return outcome_status_message(self.search_outcome or "", error_code=self.error_code)
        if self.api_error:
            return f"Error API: {self.api_error[:120]}"
        if not self.request_executed:
            return "Búsqueda no ejecutada"
        if self.prospeo_results == 0:
            if self.search_outcome == SEARCH_OUTCOME_NO_RESULTS:
                return "0 resultados reales"
            return "0 resultados reales"
        if self.valid_results == 0:
            return f"{self.prospeo_results} de Prospeo, 0 válidos tras filtros"
        return f"{self.valid_results} válidos de {self.prospeo_results} Prospeo"
