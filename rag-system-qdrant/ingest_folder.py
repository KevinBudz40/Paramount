"""
ingest_folder.py â€” Bulk folder ingestion into the RAG knowledge base.

Usage:
    python ingest_folder.py "C:\\Paramount\\10-1"
    python ingest_folder.py "C:\\Paramount\\10-1" --chroma ./qdrant_db --log ingest_log.txt

Safe to stop and restart â€” already-ingested files are tracked in ingest_tracker.db
and will never be re-processed.

MD5 dedup: files whose content is identical to an already-ingested file are skipped
even when they live in a different folder, preventing duplicate chunks in the KB.
"""
import argparse
import email as _email_stdlib
import hashlib
import html
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
import zipfile
from datetime import datetime
from pathlib import Path

# â”€â”€ Resolve project root so rag.* imports work regardless of cwd â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

from rag.chunker import _chunk_text, _extract_zip, load_document
from rag.store import add_chunks, doc_count

# â”€â”€ Constants â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_DEFAULT_CHROMA = str(_SCRIPT_DIR.parent / "qdrant_db")
_BACKOFF_DELAYS = [30, 60, 120]   # seconds between retries on rate-limit errors
_INTER_FILE_DELAY = 1.0            # seconds between embedding calls
_MIN_FILE_BYTES = 1024             # skip files smaller than 1 KB

# Explicit allowlist â€” ingestible file types
# [2026-09-23] Added .msg and .eml — Asana task attachments often contain project emails worth indexing
_ALLOWED = {“.pdf”, “.docx”, “.xlsx”, “.csv”, “.pptx”, “.txt”, “.md”, “.json”,
            “.svg”, “.vtt”, “.msg”, “.eml”}

# Directory name fragments that cause a whole subtree to be skipped
# [2026-09-23] Added "sql" — SQL scripts moved to asana/sql subfolder, not useful for RAG
_SKIP_DIR_FRAGMENTS = {"node_modules", ".git", "__pycache__", ".venv", "sql"}

# Absolute path prefixes to exclude entirely (case-insensitive on Windows)
_SKIP_PATH_PREFIXES: list[str] = []

# Specific filenames (stem only, case-insensitive) to always skip
# [2026-09-23] Added Asana attachment data dumps — raw extracts with no analytical value for RAG
_SKIP_FILENAMES = {
    "r598 - cash detail report.xlsx",
    # Massive raw extracts
    "dev5_est_usd_stu_nov25.xlsx",
    "erp paramount wall walk digital board.xlsx",
    "project_cost_1032.xlsx",
    "customers p1 report 071725.xlsx",
    "customers p1 report 071625.xlsx",
    "copy of xxcbs_stu_remap_fusion_bal.xlsx",
    "cbs news department mapping project 2025-final 02.02.26- michele.xlsx",
    "cbs news department mapping project 2025-revised 02.23.26- michele.xlsx",
    "ebs ap paid duplicate invoice header details.xlsx",
    "fixed_asset_recon_report_ebs_pg_us_corp.xlsx",
    "corporate - data validation fusion v sap all currencies_dec 24.xlsx",
    "corporate - data validation fusion v sap all currencies_dec 24_v1 dev6_012726_oracle.xlsx",
    "corporate - data validation fusion v sap all currencies_mar 25_v1 dev6 feb 1 2026.xlsx",
    "corporate - data validation fusion v sap all currencies_mar 25_v1 dev6.xlsx",
    "sap ppc_fbl5n_open ar as of 20260731_submitted.xlsx",
    "ris load excel.xlsx",
    "account segment mapping ebs to cloud coa.xlsx",
    "p1 segment value mapping ebs to cloud coa.xlsx",
    # Auto-generated system filenames
    "zi315_ap_inv_extract_open_erp_hdr_20260819092025.xlsx",
    "zi315_ap_inv_extract_open_erp_itm_20260819092025.xlsx",
    "zi315_ap_inv_extract_open_ppc_hdr_20260819105911.xlsx",
    "zi315_ap_inv_extract_open_ppc_itm_20260819105911.xlsx",
    "uncacp20250922000012.xlsx",
    "arzzcp20250723180959.v1.xlsx",
    "fusion_irtr037_viacom_acl_payroll__00000248965_analysis.xlsx",
    "fusion_irtr037_viacom_acl_payroll__00000251325_analysis.xlsx",
    "fusion_irtr037_viacom_payroll__00000245175_analysis.xlsx",
    "fusion_irtr037_viacom_payroll__00000247145_analysis.xlsx",
    "fusion_irtr037_viacom_payroll__00000248745_analysis.xlsx",
    "tvr58910_unpack_nov2025.xlsx",
    "tvr58910_unpack_nov202510.xlsx",
    "trc58810_unpack_nov20258.xlsx",
    "pg_prism_dist_11_24001.xlsx",
    # "Analysis for Balancing by Company" series — balance reconciliation data, not docs
    "est_analysis for balancing by company.xlsx",
    "prism_analysis for balancing by company.xlsx",
    "trc_analysis for balancing by company.xlsx",
    "tvr analysis for balancing by company.xlsx",
    "it2_analysis for balancing by company.xlsx",
    "cp_analysis for balancing by company.xlsx",
    # Raw report output snapshots
    "pg_fah_rpt058_supportingreferences_rpt (9).xlsx",
    "importaccountingtrx_exceptions_834498.xlsx",
    "subscription tax report tax oct25 sample.xlsx",
    "3pr_sample_report_query.xlsx",
    "pg_cm_rpt304_outstanding_check_balance_rpt_output(12).xlsx",
    "pg_cm_rpt304_outstanding_check_balance_rpt_output(2).xlsx",
    "pg_cm_rpt304_outstanding_check_balance_rpt_output_feb2.xlsx",
    "copy of pg_cm_rpt304_outstanding_check_balance_rpt_output(7).xlsx",
    # [2026-09-23] Group A — system-generated interface/extract files
    "fagll03.xlsx",
    "assetadditionsresp.xlsx",
    "zi641psle030525210459_expense.xlsx",
    "zi642pslc041625210456_check.xlsx",
    "zi643psld20250726040010.xlsx",
    "zi644psld20250726040010.xlsx",
    "d_ora_112520251000_778002_dat.xlsx",
    # [2026-09-23] Group B — report output snapshots
    "enterprisestructurereport_enterprise structure report - aug 12.xlsx",
    "enterprisestructurereport_p2.xlsx",
    "pg_p2_enterprise structure report.xlsx",
    "globalslaacctanalysisreport.xlsx",
    "pg_gl_rpt085_posting_register_rpt_output_dev4.xlsx",
    "pg_gl_rpt085_posting_register_rpt_output_dev2.xlsx",
    "pg_ap_rpt360_cleared_ap_items_by_intercompany_code_rpt_output dev2.xlsx",
    "pg_ap_rpt360_cleared_ap_items_by_intercompany_code_rpt_output_latest.xlsx",
    "feb18_pg_cm_rpt304_outstanding_check_balance_rpt_output.xlsx",
    "report output_feb25_pg_cm_rpt328_summarized_check_rpt_output.xlsx",
    "pg_cm_rpt328_summarized_check_rpt_output.xlsx",
    "pg_cm_rpt328_summarized_check_rpt_output (7).xlsx",
    "screengrab for posted report.xlsx",
    "standard report.xlsx",
    "auditreport_banktrxrule.xlsx",
    "otc report details _updated 8.2025 (6).xlsx",
    "otc report details _updated 8.2025.xlsx",
    # [2026-09-23] Group C — PO/AP error report data dumps
    "latest_ebs_pivot_pg po recipts error report.xlsx",
    "ebs_pivot_pg po error report (2).xlsx",
    "sap_via_pivot_pg po error report (2).xlsx",
    "sap_ppc_pivot_pg po error report (2).xlsx",
    "sap_ppc_pivot_pg po receipts errors report.xlsx",
    "sap_ppc_pivot_pg po receipts errors report (1).xlsx",
    "sap_viacom_po contract purchase agreements validation error report.xlsx",
    "sap_viacom_pivot_pg po receipts errors report.xlsx",
    # [2026-09-23] Group D — mapping function error data dumps
    "prism_stsd_consolidated_mappingfunction_errors.xlsx",
    "mapping function errors consolidated.xlsx",
    "02dec2025 mapping function errors_consolidated.xlsx",
    "consolidated mapping function errors_08dec2025.xlsx",
    "prism_dev7_dec25_error_11feb.xlsx",
    "dev7_dec25_prism_error.xlsx",
    # [2026-09-23] Group E — missing/duplicate data extracts
    "missing suppliers_paidinv viacom.xlsx",
    "missing suppliers_paidinv ppc.xlsx",
    "missing suppliers_openinv_viacom.xlsx",
    "missing suppliers_paid ebs.xlsx",
    "missing customer viacom.xlsx",
    "missing customers ebs.xlsx",
    "missing customers.xlsx",
    "viacom ap paid inv duplicate error records.xlsx",
    "duplicate invoicespaidinv viacom.xlsx",
    "duplicatee invoices_paidinv ppc.xlsx",
    "viacom invalid suppliers.xlsx",
    "ebs errored employees.xlsx",
    "ppc errored employees.xlsx",
    "viacom errored employees.xlsx",
    "23424-open-invoices.xlsx",
    # [2026-09-23] Group F — CMV-CTD balance sheet dated series
    "cmv-ctd bal sheet - pnl sv_jk_12152025.xlsx",
    "cmv-ctd bal sheet - pnl sv_jk_12162025.xlsx",
    "cmv-ctd bal sheet - pnl sv_jk_12222025.xlsx",
    # [2026-09-23] .csv data dumps — PRISM extracts, error logs, system interface files
    "pg_prism_stsd_11_724.csv",
    "pg_customer_classification_report.csv",
    "pg_prism_stsd_11_051.csv",
    "importaccountingtrx_exceptions_834476.csv",
    "pg_prism_stsd_10_921_excep.csv",
    "coa_function& depart segment errors_29jan26.csv",
    "ptp supplier bank - dev5.csv",
    "ptp supplier bank - dev6.csv",
    "atpbulkprocess_error.csv",
    "atpbulkprocess_error_11620260303155741_4212_1_20260303t155822327494z.csv",
    "pg_fa_into_001_fa_assets_utilization_to_epm_rpt_etext.csv",
    "msp_data_extract_client_10002_20250910_124519.csv",
    "showtime_insert_records.csv",
    "xlatrxl_11020260109153825.csv",
    "xlatrxl_11120260127112403.csv",
    "xlatrxl_11820260114181716.csv",
    "xlatrxh_11020260109153825.csv",
    "xlatrxh_11120260127112403.csv",
    "xlatrxh_11820260114181716.csv",
    "user_preference (1)_dev2_review.csv",
    "prod_july users with updated user preferences_05-jul-2026.csv",
    "cbstelevisionstations_23037_20251212121100_c-fringefile.csv",
    "cbstelevisionstations_23037_20251212001700_a-fringefile.csv",
    "intercompanyaccountdetails_intercompany account details report(1).csv",
    "intercompanytransactionsummary_intercompany transaction summary report.csv",
    "ora_inv_unit_of_measure_std_conversion.csv",
    "ora_inv_unit_of_measure_std_conversion-1001.csv",
    "inv_unit_of_measure.csv",
    "inv_unit_of_measure-1001.csv",
    "inv_unit_of_measure_class.csv",
    "coa_function_errors_test.csv",
    "lac_updated function error message.csv",
    "mediapulse_coa_function_errors_29jan26.csv",
    "mediapulse_coa_error.csv",
    "error-2.csv",
    "msp_to_oracle_topsides_20250910124335.csv",
    "fahglrevenue_int009_i_topside_coralmsp_20250908220026.csv",
    "fahglrevenue_int009_i_topside_coralmsp_20250908220035.csv",
    "fahglrevenue_int009_i_topside_coralmsp_20250908220040.csv",
    "budget_sportsnetwork_department_03022026 (1).csv",
    "broadcast_new_records.csv",
    "1437 extract.csv",
    "2186_dept.csv",
    # [2026-09-23] .txt data dumps — SAP/system interface files and report outputs
    "outbound file from sap to studio billing hub_mfc_full load.txt",
    "optpi4655_tc_vendor_20250804220016.txt",
    "i017tds000000287620250917193005.txt",
    "i017tds000000287120250912193006.txt",
    "ppc_fg_cost_object_wbse_upload_20258281043043.txt",
    "pg_ar_rpt415_edi_report_dev3.txt",
    "pg_ar_rpt415_edi_report_dev10.txt",
    "zi641psle030525210459_expense.txt",
    "zi642pslc041625210456_check.txt",
    "zi643psld20250726040010.txt",
    "zi644psld20250726040010.txt",
    "zi641sae061525223409_e5kx_lo-1028286.txt",
    "zi642sac061325220725_e5ve_lo-1028595.txt",
    "cbs cash requirement detail report.txt",
    "msp_data_extract_client_10000_20250905_142035.txt",
    "msp_data_extract_client_10001_20250905_142035.txt",
    "msp_data_extract_client_10002_20250905_142035.txt",
    # [2026-09-23] Group H — large raw data files (>1 MB, clearly data not design)
    "address reconciliation.xlsx",
    "consolidated sr report - 04-jun-2026.xlsx",
    "department segment errors_29jan26.xlsx",
    "copy of cbsi code combinations wip.xlsx",
    "paris structure 10-23-25.xlsx",
    "raid-5177_cbs studios open item report jul 2025 with comparison_andras.xlsx",
    "prod7.1 cbsi fixed asset schedule 06.30.2026 (2026).xlsx",
    "a2510 capex non-cash accrual - with pivots(full3).xlsx",
    "p3.7.1 cbsi pg us corp - ebs - recon - final.xlsx",
    "globalslaacctanalysis_globalslaacctanalysisreport.xlsx",
    "ibam-bank-accounts-file_v5_v.01.xlsx",
    "09.22.hv.us.residuals. source 6.xlsx",
    "mfcs created in cy 2025.xlsx",
    "ibam-bank-accounts-file_v5_v.01_mla.xlsx",
    "ibam-bank-accounts-file_v5_v.01_mla (2).xlsx",
    "idr domains & count.xlsx",
    "p3.7.1 cbsi fixed asset accounting recon schedule 04.30.2026 (2026).xlsx",
    "raid-5177_legacy viacom & corporate open item report_jul'25_andras.xlsm",
    "asanna_6688_cmv geography issue summary_updated.xlsx",
    "p3.7.1 cbsi fixed asset accounting recon schedule 2906 rerun 04.30.2026.xlsx",
    "popurchaseorderimporttemplate (1) (1)_174958_val_rep (1).xlsm",
    "dev2_validate user preferences for 5 users_03-jul-2026.xlsx",
    "viacom customers with wht codes assigned 2 july 25 (6).xlsx",
    "dev5 all (updated wrm 19-dec-25) (1).xlsx",
    "pg_july go live_users_load user preferences_01-jul-2026.xlsx",
    "pg_ap_rpt315_supplier_banks_report_output dev2.xlsx",
    "pg_est_usd_passback_analysis.xlsx",
    "pg_est_cad_passback_function error.xlsx",
    "intra bs sign off_11'25.xlsx",
    "asanna_6887_entity_4276_sv_updated.xlsx",
    "pg cm statement analysis report_output latest.xlsx",
    "global payroll account list_08.11.2025.xlsx",
    "paramount substitution 062525.xlsx",
    "viacom knb1 payment terms for extract.xlsx",
    "raid-5177_cbs open item report with comparison jul25_andras.xlsx",
    "lockboxapril2026golive.xlsx",
    "po consolidated recon report full load prod 03072026 _verification and confirmation.xlsx",
    "pg cm statement analysis report_output_uat.xlsx",
    "cmv geography issue summary.xlsx",
    "issue_details and observations - from gopal.xlsx",
    "april-1 fixed asset samples_without bonus book.xlsx",
    "monthly saas amortization tracker new _apr.xlsx",
    "april-1 fixed asset samples_with bonus book.xlsx",
    "dec-24 tb 3650.xlsx",
    "10. lob3100 detailed q2.xlsx",
    # [2026-09-23] Group I — analysis/test/recon data files
    "intercompany trading partner test - fusion validation differences viacom mn.xlsx",
    "ris dev-7 analysis.xlsx",
    "asanna_6877_entity_4276_sv.xlsx",
    "final paramount global negative cash entry analysis - jun 25 (002).xlsx",
    "pr_analysis for fringefiles.xlsx",
    "pr_analysis for fringefiles_18dec25.xlsx",
    "raid-5177_open item from reconciliations paramount 2025 jul_andras.xlsm",
    "cbsi_cashflow oibda to gross cash cbsi 2024-12_excel.xlsx",
    "tb - p&l sga vs opex variance.xlsx",
    "tb - p&l sga vs opex variance - sent.xlsx",
    "purchaseordervalidation.xlsx",
    "goodsreceiptvalidation.xlsx",
    "ap check balance report test data dev1.xlsx",
    "trc_nov25_analysis.xlsx",
    "lac_viacom_dec_analysis.xlsx",
    "lac viacom dev-7 analysis.xlsx",
    "cbs network_09.12.25_analysis.xlsx",
    "1302 2026 unapplied cash.xlsx",
    "3jpm_vam_reconciliation august 2025.xlsx",
    "prism_mapping_function issue.xlsx",
    "po-notconverted-projectbased.xlsx",
    "p3_ebs_project_cost_cts_data_12_31_25_recon.xlsx",
    "p3_ebs_project_budget_cts_data_12_31_25_recon - copy.xlsx",
    "p3_ebs_project_header_cts_data_12_31_25_recon.xlsx",
    "sap open invoices p3 reconciliation.xlsx",
    "fusion to sap reconciliation dev4 dec'24.xlsx",
    "dec25_dev7_prism_cvr issue.xlsx",
    "p2 feb - organization - validation report.xlsx",
    "agingreport_testdata.xlsx",
    # [2026-09-23] Group J — unapplied receipt/cash data series
    "unapplied receipts accounting entries - converted-post go-live_v2.xlsx",
    "unapplied receipts accounting entries - converted-post go-ive 24 sep 25 sw.xlsx",
    "unapplied receipts accounting entries - converted-post go-ive.xlsx",
    "ar unapplied receipts ebs.xlsx",
    "or - unapplied receipt register.xlsx",
    "unapplied receipt register (1).xlsx",
    "unapplied receipt register.xlsx",
    "lockbox data files 092925.xlsx",
    # [2026-09-23] Group K — historical P&L / balance sheet snapshots
    "pg income statement (2).xlsx",
    "pg income statement v3.xlsx",
    "pg income statement v3 (4).xlsx",
    "pg income statement v3 (3-dec-2025).xlsx",
    "pg income statement v4_raid_7293_issue#4.xlsx",
    "pg income statement v4_raid_7293_issue#4 - ss recon.xlsx",
    "pg income statement v4_raid_6279.xlsx",
    "raid_7293_pg income statement v4.xlsx",
    "raid_7293_pg balance sheet v4.xlsx",
    "raid_7293_updated_report_pg balance sheet v4.xlsx",
    "pg trial balance .xlsx",
    "pg trial balance_dev4.xlsx",
    "cbsi_gaap cashflow 2024-12_manually prepared in excel and loaded to cbsi tm1.xlsx",
    "cbs broadcasting fy 2025 cash flow detail.xlsx",
    "copy of zglobal_direct_monthly_cash_flow_022824 v4.xlsx",
    # [2026-09-23] Group L — SAP/EBS extract/misc data
    "xxcbsi_ar_broadcast_customers_latest.xlsx",
    "supplier_ext_bank_report.xlsx",
    "customerexternal_bank_report.xlsx",
    "po_receipt_success_record_29oct2025.xlsx",
    "po receipt error_29oct2025.xlsx",
    "ebs ap open invoices - project related errors.xlsx",
    "ebs receipts - missing customer .xlsx",
    "fahgl_int145_i_newspath_25181201.nwp.xlsx",
    "analysis_newspath_fahgl_int145_i_newspath_2512061701.xlsx",
    "newspath_analysis_fahgl_int145_i_newspath_2512201801.xlsx",
    "lac_to_vmn_sap_mn842transition-20250528124147697246(ricenew60).xlsx",
    "lac_to_ppc_ppc842transition-20250527122809850536(ricefw-467).xlsx",
    "ppc_project_header_extract.xlsx",
    "ppc_paramount customer_extract_delta_oracle_fusion_20251015.xlsx",
    "ppc_paramount customer_contacts extract_delta_oracle_fusion_20251015.xlsx",
    "fusion-prod-bank_branch-summary-20260326.xlsx",
    "copy of 12-03-2025_corporate payments.xlsx",
    "copy of 12-03-2025_paramount payments.xlsx",
    "copy of 12-03-2025_viacom payments.xlsx",
    "cbsi_dmf_prevalidation_dev9_29apr.xlsm",
    "sap ap open invoices error report.xlsm",
    "viacom_jul26_dmf_prevalidation_dev9_29apr.xlsx",
    "sapppc-fusion-fbdi-oct2026-supplier address-non usxlsx.xlsx",
    "zti115_ebs_mt940.xlsx",
    "zti115_ebs_bai.xlsx",
    # [2026-09-23] BNA fixed asset production data dumps
    "bna final_2025 with bonus_prod_5-11-26.xlsm",
    "bna final_2025 without bonus_prod_5-15-26.xlsm",
    # [2026-09-23] Group G — raw AR/AP financial data extracts
    "ar open items with 4th currency.xlsx",
    "invoiced prior to vendor setup q3 2025 viacom.xlsx",
    "ap wire analysis_dev2 instance.xlsx",
    "ap wire analysis output.xlsx",
    "otbi ap wire.xlsx",
    "ebs ap open inv_withorg.xlsx",
    # [2026-09-23] Group M — FA Roll Forward raw data series (versioned balance extracts)
    "pg_fa_roll_forward_asset_baalancev5.00.xlsx",
    "pg_fa_roll_forward_asset_baalancev4.21.xlsx",
    "pg_fa_roll_forward_asset_baalancev4.2.xlsx",
    "pg_fa_roll_forward_asset_baalancev4.1.xlsx",
    "pg_fa_roll_forward_asset_baalancev3.00.xlsx",
    "pg_fa_roll_forward_asset_baalancev2.00.xlsx",
    "pg_fa_roll_forward_asset_baalancev1.00.xlsx",
    "fa rollforward report-8-13.xlsx",
    "ytd fa rollforward report-8-13.xlsx",
    "ytd fa rollforward report.xlsx",
    "period fa rollforward.xlsx",
    # [2026-09-23] Group N — RPT output runs (report execution results, not FDDs)
    "pg_ap_071_payment_details_rpt_rpt071-output.xlsx",
    "pg_pai_detail_costs_rpt_output.xlsx",
    "pg_exp_rpt002_audit_productivity_rpt_ouput.xlsx",
    "pg_exp_rpt002_audit_productivity_rpt_ouput_260925.xlsx",
    "final - usbujulygolive-createaccounting_create accounting report .xlsx",
    "draft-usbujulygolive-createaccounting_create accounting report.xlsx",
    "pg_cm_135_negativecash_rpt_output_all legal entity.xlsx",
    "pg_cm_135_negativecash_rpt_output_segment.xlsx",
    "pg_cm_135_negativecash_rpt_output_cbs studios.xlsx",
    "pg_cm_135_negativecash_rpt_output_paramount.xlsx",
    "pg_cm_135_negativecash_rpt_output-10.17-dev4.xlsx",
    "pg_cm_rpt239_bank_statement_import_status_rpt_bank import status report (1).xlsx",
    "pg_cm_rpt239_bank_statement_import_status_rpt_bank import status report.xlsx",
    "sr report ris_success.xlsx",
    "fullextract_accrual_fringe.xlsx",
    "coral analysis_coralmsp_202512100000000.xlsx",
    "paramountsampledigital aging for sap billings.xlsx",
    # [2026-09-23] Group O — versioned duplicates (older versions superseded by newer)
    "negative cash report_design_template_v1.1.xlsx",
    "negative cash report_design_template_v1.2.xlsx",
    "intercompany org list and scenarios p2_final 10-10-25.xlsx",
    "boa lb keying fields.xlsx",
    "bank account master_v2.5_virtual account.xlsx",
    "ap140-payment disbursement report fdd (1).xlsx",
    "rpt373 - pg gl bank account analysis report fdd.v1.xlsx",
    "rpt373 - gl bank account analysis report fdd.v1.xlsx",
    "rpt_067_ptp_ap_dashboard_fdd_v.1.xlsx",
    "rpt086_rpt113_trial balance_fdd.xlsx",
    "invoice special approval group report fdd 1.0.xlsx",
    "pg ap cash requirement report fdd.v1.xlsx",
    "rpt345_pg_payables_tax_ report_v1.1.xlsx",
    "rpt344_pg_ar_tax report_v1.1.xlsx",
    "invoices_on_matching_hold_fdd.xlsx",
    "pg ap cash requirement report fdd.v1.1.xlsx",
    "total paid ach checks fdd.v1.1.2.xlsx",
    # [2026-09-23] Group P — Q3 error/defect data series
    "q3_2186_errors.xlsx",
    "q3_2249_errors.xlsx",
    "q3_2248_dept_errors.xlsx",
    "2027_costs_lines_duplicated.xlsx",
}


# â”€â”€ SQLite tracker â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _open_db(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE IF NOT EXISTS ingested (
            path        TEXT PRIMARY KEY,
            status      TEXT,
            chunks      INTEGER,
            ingested_at TEXT,
            error       TEXT,
            md5_hash    TEXT
        )
    """)
    # Add md5_hash column to existing DBs that pre-date this change
    try:
        con.execute("ALTER TABLE ingested ADD COLUMN md5_hash TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    con.commit()
    return con


def _is_done(con: sqlite3.Connection, path: str) -> bool:
    row = con.execute(
        "SELECT status FROM ingested WHERE path = ?", (path,)
    ).fetchone()
    return row is not None and row[0] == "ok"


def _is_duplicate(con: sqlite3.Connection, md5: str) -> str | None:
    """Return the path of an already-ingested file with the same MD5, or None."""
    row = con.execute(
        "SELECT path FROM ingested WHERE md5_hash = ? AND status = 'ok' LIMIT 1", (md5,)
    ).fetchone()
    return row[0] if row else None


def _record(con: sqlite3.Connection, path: str, status: str,
            chunks: int = 0, error: str = "", md5: str = "") -> None:
    con.execute(
        """INSERT INTO ingested (path, status, chunks, ingested_at, error, md5_hash)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(path) DO UPDATE SET
               status=excluded.status, chunks=excluded.chunks,
               ingested_at=excluded.ingested_at, error=excluded.error,
               md5_hash=excluded.md5_hash""",
        (path, status, chunks, datetime.now().isoformat(timespec="seconds"), error, md5),
    )
    con.commit()


# â”€â”€ File collection â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _collect_files(root: Path) -> list[Path]:
    """
    Recursively collect all ingestible files under root.
    Zip archives are expanded into a temp directory and their contents collected;
    the temp dir is cleaned up when this function returns (zips are NOT themselves
    added to the list â€” their extracted files are).
    """
    results: list[Path] = []
    _walk(root, results, temp_roots=[])
    return results


def _walk(folder: Path, out: list[Path], temp_roots: list[Path]) -> None:
    for entry in sorted(folder.rglob("*")):
        if not entry.is_file():
            continue

        parts_lower = [p.lower() for p in entry.parts]

        # Skip macOS / Windows metadata noise
        if any(p.startswith("__macosx") or p.startswith(".") for p in parts_lower):
            continue

        # Skip subtrees we never want
        if any(frag in parts_lower for frag in _SKIP_DIR_FRAGMENTS):
            continue

        # Skip explicitly excluded path prefixes
        entry_lower = str(entry).lower()
        if any(entry_lower.startswith(prefix) for prefix in _SKIP_PATH_PREFIXES):
            continue

        suffix = entry.suffix.lower()

        if suffix == ".zip":
            tmp = Path(tempfile.mkdtemp(prefix="rag_ingest_zip_"))
            temp_roots.append(tmp)
            try:
                _extract_zip(entry, tmp)
                _walk(tmp, out, temp_roots)
            except Exception as exc:
                print(f"  [WARN] Could not expand {entry.name}: {exc}")
            continue

        if suffix not in _ALLOWED:
            continue

        if entry.name.lower() in _SKIP_FILENAMES:
            continue

        if entry.stat().st_size < _MIN_FILE_BYTES:
            continue

        out.append(entry)


# â”€â”€ Email extraction (.msg / .eml) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# [2026-09-23] Added to support .msg/.eml attachments downloaded from Asana tasks

def _html_to_text(html_body: str) -> str:
    “””Strip HTML tags and decode entities for plain-text indexing.”””
    text = re.sub(r”<[^>]+>”, “ “, html_body)
    text = html.unescape(text)
    return re.sub(r”\s+”, “ “, text).strip()


def _extract_eml(path: Path) -> str:
    “””Extract subject + body text from a .eml file (RFC 2822).”””
    with open(path, “rb”) as f:
        msg = _email_stdlib.message_from_binary_file(f)

    subject = msg.get(“Subject”, “”)
    sender  = msg.get(“From”, “”)
    date    = msg.get(“Date”, “”)
    parts: list[str] = [f”Subject: {subject}”, f”From: {sender}”, f”Date: {date}”, “”]

    for part in msg.walk():
        ct = part.get_content_type()
        if part.get_content_disposition() == “attachment”:
            continue
        if ct == “text/plain”:
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or “utf-8”
                parts.append(payload.decode(charset, errors=”replace”))
        elif ct == “text/html”:
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or “utf-8”
                parts.append(_html_to_text(payload.decode(charset, errors=”replace”)))

    return “\n”.join(parts)


def _extract_msg(path: Path) -> str:
    “””Extract subject + body text from an Outlook .msg file using extract-msg.”””
    try:
        import extract_msg  # type: ignore
    except ImportError:
        raise ImportError(
            “extract-msg is required for .msg files: pip install extract-msg”
        )
    with extract_msg.openMsg(str(path)) as m:
        subject = m.subject or “”
        sender  = m.sender or “”
        date    = str(m.date or “”)
        body    = m.body or “”
        # Fall back to HTML body if plain text is empty
        if not body.strip() and hasattr(m, “htmlBody”) and m.htmlBody:
            charset = “utf-8”
            raw = m.htmlBody
            if isinstance(raw, bytes):
                raw = raw.decode(charset, errors=”replace”)
            body = _html_to_text(raw)
    return “\n”.join([f”Subject: {subject}”, f”From: {sender}”, f”Date: {date}”, “”, body])


# â”€â”€ Ingestion â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _ingest_file(
    path: Path,
    source_label: str,
    chroma_dir: str,
    retries: int = 3,
) -> int:
    """
    Load, chunk, and embed one file. Returns number of chunks stored.
    Retries with exponential backoff on rate-limit-like errors.
    """
    _BATCH_SIZE = 500  # stay well under ChromaDB's 5461 limit

    # [2026-09-23] Route .msg/.eml through dedicated extractors instead of load_document
    if path.suffix.lower() == ".eml":
        text = _extract_eml(path)
    elif path.suffix.lower() == ".msg":
        text = _extract_msg(path)
    else:
        text = load_document(path)
    if not text or not text.strip():
        raise ValueError("empty content")

    chunks_text = _chunk_text(text)
    chunks = [
        {"text": t, "source": source_label, "chunk_index": i}
        for i, t in enumerate(chunks_text)
    ]

    total = 0
    for batch_start in range(0, len(chunks), _BATCH_SIZE):
        batch = chunks[batch_start: batch_start + _BATCH_SIZE]
        for attempt in range(retries + 1):
            try:
                add_chunks(batch, doc_meta={}, persist_dir=chroma_dir)
                total += len(batch)
                break
            except Exception as exc:
                msg = str(exc).lower()
                is_rate_limit = any(k in msg for k in ("rate", "429", "too many", "quota"))
                if is_rate_limit and attempt < retries:
                    delay = _BACKOFF_DELAYS[min(attempt, len(_BACKOFF_DELAYS) - 1)]
                    print(f"  [RATE LIMIT] waiting {delay}s before retry {attempt + 1}/{retries}â€¦")
                    time.sleep(delay)
                else:
                    raise

    return total


# â”€â”€ Main â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk ingest a folder into the RAG KB.")
    parser.add_argument("folder", help="Root folder to ingest")
    parser.add_argument("--chroma", default=_DEFAULT_CHROMA,
                        help=f"ChromaDB directory (default: {_DEFAULT_CHROMA})")
    parser.add_argument("--db", default="ingest_tracker.db",
                        help="SQLite tracker database (default: ingest_tracker.db)")
    parser.add_argument("--log", default="ingest_log.txt",
                        help="Log file (default: ingest_log.txt)")
    parser.add_argument("--retry-errors", action="store_true",
                        help="Re-process all files with status=error in the tracker DB")
    args = parser.parse_args()

    root = Path(args.folder).resolve()
    if not root.exists():
        print(f"ERROR: folder not found: {root}")
        sys.exit(1)

    print(f"\nRAG Folder Ingestion")
    print(f"  Source : {root}")
    print(f"  ChromaDB: {args.chroma}")
    print(f"  Tracker : {args.db}")
    print(f"  Log     : {args.log}")
    if args.retry_errors:
        print(f"  Mode    : retry errors")
    print()

    # â”€â”€ Open tracker + log â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    con = _open_db(args.db)
    log = open(args.log, "a", encoding="utf-8")

    def _log(line: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        log.write(f"[{ts}] {line}\n")
        log.flush()

    # â”€â”€ Collect files â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if args.retry_errors:
        # Build a map of source_label -> actual path from the scanned files,
        # then filter to only those marked error in the tracker.
        print("Scanning for files to build path mapâ€¦")
        temp_roots: list[Path] = []
        all_scanned: list[Path] = []
        _walk(root, all_scanned, temp_roots)

        # Build label -> path lookup
        label_to_path: dict[str, Path] = {}
        for fpath in all_scanned:
            try:
                label = fpath.relative_to(root).as_posix()
            except ValueError:
                label = fpath.name
            label_to_path[label] = fpath

        # Fetch error entries from tracker
        rows = con.execute(
            "SELECT path FROM ingested WHERE status = 'error'"
        ).fetchall()
        error_labels = [r[0] for r in rows]

        all_files = []
        missing = []
        for label in error_labels:
            if label in label_to_path:
                all_files.append(label_to_path[label])
            else:
                missing.append(label)

        if missing:
            print(f"  {len(missing)} error entries have no matching file on disk (skipped)")
        total = len(all_files)
        print(f"Found {total} error files to retry.\n")
    else:
        print("Scanning for filesâ€¦")
        temp_roots = []
        all_files = []
        _walk(root, all_files, temp_roots)
        total = len(all_files)
        print(f"Found {total} ingestible files.\n")

    if total == 0:
        print("Nothing to ingest.")
        return

    _log(f"=== Session start: {root} ({total} files) retry={args.retry_errors} ===")

    # â”€â”€ Process â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    ingested = skipped_done = skipped_empty = skipped_dupe = errors = 0

    for idx, fpath in enumerate(all_files, 1):
        # Use path relative to root as the source label (preserves folder context)
        try:
            source_label = fpath.relative_to(root).as_posix()
        except ValueError:
            # File is inside an extracted zip temp dir â€” use filename only
            source_label = fpath.name

        print(f"[{idx}/{total}] {source_label}", end="", flush=True)

        # Skip already done (same path)
        if _is_done(con, source_label):
            print("  -> already ingested, skipping")
            _log(f"SKIP (done) {source_label}")
            skipped_done += 1
            continue

        # MD5 dedup â€” skip files whose content already exists under a different path
        file_md5 = _md5(fpath)
        existing = _is_duplicate(con, file_md5)
        if existing:
            print(f"  -> duplicate of '{existing}', skipping")
            _log(f"SKIP (dupe of {existing}) {source_label}")
            _record(con, source_label, "duplicate", md5=file_md5)
            skipped_dupe += 1
            continue

        try:
            n = _ingest_file(fpath, source_label, args.chroma)
            if n == 0:
                print("  -> empty, skipped")
                _log(f"SKIP (empty) {source_label}")
                _record(con, source_label, "empty", md5=file_md5)
                skipped_empty += 1
            else:
                print(f"  -> {n} chunks")
                _log(f"OK {n} chunks  {source_label}")
                _record(con, source_label, "ok", chunks=n, md5=file_md5)
                ingested += 1
                time.sleep(_INTER_FILE_DELAY)

        except KeyboardInterrupt:
            print("\n\nInterrupted â€” progress saved. Re-run to continue.")
            _log("=== Interrupted by user ===")
            break

        except Exception as exc:
            err = str(exc)
            print(f"  -> ERROR: {err}")
            _log(f"ERROR {source_label}  {err}")
            _record(con, source_label, "error", error=err)
            errors += 1

    # â”€â”€ Summary â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    total_kb = doc_count(args.chroma)
    summary = (
        f"\nDone.\n"
        f"  Ingested : {ingested}\n"
        f"  Skipped  : {skipped_done} already done, {skipped_dupe} duplicates, {skipped_empty} empty\n"
        f"  Errors   : {errors}\n"
        f"  KB total : {total_kb} chunks\n"
    )
    print(summary)
    _log(f"=== Session end â€” ingested={ingested} skipped={skipped_done} errors={errors} total_kb={total_kb} ===")

    log.close()
    con.close()

    # Clean up any zip temp dirs created during file collection
    for tmp in temp_roots:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

