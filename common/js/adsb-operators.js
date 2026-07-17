/* ADS-B operator table — ICAO 3-letter -> ATC telephony callsign.
   SEED (major carriers). Run scripts/build-adsb-operators.py with an OpenFlights
   airlines.dat to regenerate the full (~1500-entry) table; that overwrites this
   file. Source of the full data: OpenFlights airlines.dat (Open Database License). */
globalThis.OASIS_OPERATORS = {
  "AAL":"AMERICAN","AAY":"ALLEGIANT","ABX":"ABEX","ACA":"AIR CANADA","AFR":"AIRFRANS",
  "ANA":"ALL NIPPON","ASA":"ALASKA","ASH":"AIR SHUTTLE","ATN":"AIR TRANSPORT",
  "AWI":"AIR WISCONSIN","BAW":"SPEEDBIRD","CPA":"CATHAY","DAL":"DELTA","DLH":"LUFTHANSA",
  "EDV":"ENDEAVOR","ENY":"ENVOY","EZY":"EASY","FDX":"FEDEX","FFT":"FRONTIER FLIGHT",
  "GTI":"GIANT","HAL":"HAWAIIAN","JAL":"JAPAN AIR","JBU":"JETBLUE","JIA":"BLUE STREAK",
  "KAL":"KOREAN AIR","KLM":"KLM","NKS":"SPIRIT WINGS","QTR":"QATARI","RPA":"BRICKYARD",
  "RYR":"RYANAIR","SKW":"SKYWEST","SWA":"SOUTHWEST","UAE":"EMIRATES","UAL":"UNITED",
  "UPS":"UPS","VIR":"VIRGIN","WJA":"WESTJET"
};
