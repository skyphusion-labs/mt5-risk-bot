"""ISO 4217 currency codes, plus the metal codes ISO 4217 itself assigns."""

from __future__ import annotations

# The table CONFIRMS a pair; it never refuses a trade. A code missing here
# makes the currency limit not applicable to that symbol, which is allowed and
# recorded, so completeness is desirable but never a safety property.
_CODES = (
    "AED AFN ALL AMD ANG AOA ARS AUD AWG AZN"
    " BAM BBD BDT BGN BHD BIF BMD BND BOB BOV BRL BSD BTN BWP BYN BZD"
    " CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUP CVE CZK"
    " DJF DKK DOP DZD EGP ERN ETB EUR"
    " FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD"
    " HKD HNL HRK HTG HUF IDR ILS INR IQD IRR ISK"
    " JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT"
    " LAK LBP LKR LRD LSL LYD"
    " MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN"
    " NAD NGN NIO NOK NPR NZD OMR"
    " PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF"
    " SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD SSP STN SVC SYP SZL"
    " THB TJS TMT TND TOP TRY TTD TWD TZS"
    " UAH UGX USD USN UYI UYU UYW UZS"
    " VED VES VND VUV WST"
    " XAF XAG XAU XCD XCG XDR XOF XPD XPF XPT XSU XUA"
    " YER ZAR ZMW ZWG ZWL"
)

CURRENCY_CODES = frozenset(_CODES.split())
