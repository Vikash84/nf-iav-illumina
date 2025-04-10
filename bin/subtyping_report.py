#!/usr/bin/env python

"""
Generate an Influenza H/N subtyping report from nucleotide BLAST results for one or more genomes.
Metadata from the NCBI Influenza DB is merged in with the BLAST results to improve subtyping results and provide context for the results obtained.
For reference:
Segment 4 - hemagglutinin (HA) gene
Segment 6 - neuraminidase (NA) gene
"""

import logging
import os
import re
import sys
from collections import defaultdict
from os import PathLike
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union, Any

import typer
import numpy as np
import pandas as pd
import polars as pl
from rich.logging import RichHandler

VERSION = "2025.03.1"

logger = logging.getLogger(__name__)

app = typer.Typer()

pl.enable_string_cache(True)

IAV_SEGMENT_NAMES = {
    1: "1_PB2",
    2: "2_PB1",
    3: "3_PA",
    4: "4_HA",
    5: "5_NP",
    6: "6_NA",
    7: "7_M",
    8: "8_NS",
}

IBV_SEGMENT_NAMES = {
    1: "1_PB1",
    2: "2_PB2",
    3: "3_PA",
    4: "4_HA",
    5: "5_NP",
    6: "6_NA",
    7: "7_M",
    8: "8_NS",
}

METADATA_COLUMNS = [
    ("#Accession", str),
    ("Release_Date", pl.Categorical),
    ("Genus", pl.Categorical),
    ("Length", pl.UInt16),
    ("Genotype", str),
    ("Segment", pl.Categorical),
    ("Publications", str),
    ("Geo_Location", pl.Categorical),
    ("Host", pl.Categorical),
    ("Isolation_Source", pl.Categorical),
    ("Collection_Date", pl.Categorical),
    ("GenBank_Title", str),
]

# Column names/types/final report names
BLASTN_COLUMNS = [
    ("qaccver", str),
    ("saccver", str),
    ("pident", float),
    ("length", pl.UInt32),
    ("mismatch", pl.UInt32),
    ("gapopen", pl.UInt32),
    ("qstart", pl.UInt32),
    ("qend", pl.UInt32),
    ("sstart", pl.UInt32),
    ("send", pl.UInt32),
    ("evalue", pl.Float32),
    ("bitscore", pl.Float32),
    ("qlen", pl.UInt32),
    ("slen", pl.UInt32),
    ("qcovs", pl.Float32),
    ("stitle", str),
]

BLAST_RESULTS_REPORT_COLUMNS = [
    ("sample", "Sample"),
    ("sample_segment", "Sample Genome Segment Number"),
    ("#Accession", "Reference NCBI Accession"),
    ("Genotype", "Reference Subtype"),
    ("Genus", "Genus"),
    ("pident", "BLASTN Percent Identity"),
    ("length", "BLASTN Alignment Length"),
    ("mismatch", "BLASTN Mismatches"),
    ("gapopen", "BLASTN Gaps"),
    ("qstart", "BLASTN Sample Start Index"),
    ("qend", "BLASTN Sample End Index"),
    ("sstart", "BLASTN Reference Start Index"),
    ("send", "BLASTN Reference End Index"),
    ("evalue", "BLASTN E-value"),
    ("bitscore", "BLASTN Bitscore"),
    ("qlen", "Sample Sequence Length"),
    ("slen", "Reference Sequence Length"),
    ("qcovs", "Sample Sequence Coverage of Reference Sequence"),
    ("stitle", "Reference Sequence ID"),
    ("Segment", "Reference Genome Segment Number"),
    ("GenBank_Title", "Reference Virus Name"),
    ("Host", "Reference Host"),
    ("Geo_Location", "Reference Geo Location"),
    ("Collection_Date", "Reference Collection Date"),
    ("Release_Date", "Reference Release Date"),
]

SUBTYPE_RESULTS_SUMMARY_COLUMNS = [
    "sample",
    "Genotype",
    "H_top_accession",
    "H_type",
    "H_virus_name",
    "H_NCBI_Influenza_DB_proportion_matches",
    "H_NCBI_Influenza_DB_pident_threshold",
    "N_top_accession",
    "N_type",
    "N_virus_name",
    "N_NCBI_Influenza_DB_proportion_matches",
    "N_NCBI_Influenza_DB_pident_threshold",
]

H_COLUMNS = [
    "sample",
    "Genotype",
    "H_top_accession",
    "H_NCBI_Influenza_DB_proportion_matches",
    "H_NCBI_Influenza_DB_subtype_matches",
    "H_NCBI_Influenza_DB_total_matches",
    "H_NCBI_Influenza_DB_pident_threshold",
    "H_sample_segment_length",
    "H_top_align_length",
    "H_top_bitscore",
    "H_top_country",
    "H_top_date",
    "H_top_gaps",
    "H_top_host",
    "H_top_mismatch",
    "H_top_pident",
    "H_top_seq_length",
    "H_type",
    "H_virus_name",
]

N_COLUMNS = [
    "sample",
    "Genotype",
    "N_top_accession",
    "N_NCBI_Influenza_DB_proportion_matches",
    "N_NCBI_Influenza_DB_subtype_matches",
    "N_NCBI_Influenza_DB_total_matches",
    "N_NCBI_Influenza_DB_pident_threshold",
    "N_sample_segment_length",
    "N_top_align_length",
    "N_top_bitscore",
    "N_top_country",
    "N_top_date",
    "N_top_gaps",
    "N_top_host",
    "N_top_mismatch",
    "N_top_pident",
    "N_top_seq_length",
    "N_type",
    "N_virus_name",
]

SUBTYPE_RESULTS_SUMMARY_FINAL_NAMES = {
    "sample": "Sample",
    "Genotype": "Subtype Prediction",
    "N_type": "N: type prediction",
    "N_top_accession": "N: top match accession",
    "N_virus_name": "N: top match virus name",
    "N_top_host": "N: top match host",
    "N_top_date": "N: top match collection date",
    "N_top_country": "N: top match country",
    "N_top_pident": "N: top match BLASTN % identity",
    "N_top_align_length": "N: top match BLASTN alignment length",
    "N_top_mismatch": "N: top match BLASTN mismatches",
    "N_top_gaps": "N: top match BLASTN gaps",
    "N_top_bitscore": "N: top match BLASTN bitscore",
    "N_top_seq_length": "N: top match sequence length",
    "N_sample_segment_length": "N: sample segment length",
    "N_NCBI_Influenza_DB_proportion_matches": "N: NCBI Influenza DB subtype match proportion",
    "N_NCBI_Influenza_DB_subtype_matches": "N: NCBI Influenza DB subtype match count",
    "N_NCBI_Influenza_DB_total_matches": "N: NCBI Influenza DB total count",
    "N_NCBI_Influenza_DB_pident_threshold": "N: NCBI Influenza DB % identity threshold",
    "H_type": "H: type prediction",
    "H_top_accession": "H: top match accession",
    "H_virus_name": "H: top match virus name",
    "H_top_host": "H: top match host",
    "H_top_date": "H: top match collection date",
    "H_top_country": "H: top match country",
    "H_top_pident": "H: top match BLASTN % identity",
    "H_top_align_length": "H: top match BLASTN alignment length",
    "H_top_mismatch": "H: top match BLASTN mismatches",
    "H_top_gaps": "H: top match BLASTN gaps",
    "H_top_bitscore": "H: top match BLASTN bitscore",
    "H_top_seq_length": "H: top match sequence length",
    "H_sample_segment_length": "H: sample segment length",
    "H_NCBI_Influenza_DB_proportion_matches": "H: NCBI Influenza DB subtype match proportion",
    "H_NCBI_Influenza_DB_subtype_matches": "H: NCBI Influenza DB subtype match count",
    "H_NCBI_Influenza_DB_total_matches": "H: NCBI Influenza DB total count",
    "H_NCBI_Influenza_DB_pident_threshold": "H: NCBI Influenza DB % identity threshold",
}

# Regex to find unallowed characters in Excel worksheet names
REGEX_UNALLOWED_EXCEL_WS_CHARS = re.compile(r"[\\:/?*\[\]]+")

def most_frequent_segment():
    return pl.col("Segment").value_counts(sort=True).first()

def get_most_frequent_segment(sample_segment: dict) -> pl.Expr:
    return pl.col("sample_segment").value_counts(sort=True).first()

def parse_blast_result(
        blast_result: str,
        df_metadata: pl.DataFrame,
        regex_subtype_pattern: str,
        get_top_ref: bool,
        top: int = 3,
        pident_threshold: float = 0.85,
        min_aln_length: int = 50,
) -> Optional[Tuple[pl.DataFrame, Dict, str]]:
    logger.info(f"Parsing BLAST results from {blast_result}")

    try:
        df_filtered = (
            pl.scan_csv(
                blast_result,
                has_header=False,
                separator="\t",
                new_columns=[name for name, coltype in BLASTN_COLUMNS],
                dtypes=dict(BLASTN_COLUMNS),
            )
            .filter(
                (pl.col("pident") >= (pident_threshold * 100))
                & (pl.col("length") >= min_aln_length)
            )
            .collect(streaming=True)
        )
    except pl.exceptions.NoDataError:
        logger.warning(f"No BLAST results found in {blast_result}")
        return None
    if df_filtered.shape[0] == 0:
        logger.warning(
            f"No BLAST results found in {blast_result} >= {pident_threshold}% identity and alignment length >= {min_aln_length}"
        )
        return None
    first_qaccver = df_filtered["qaccver"][0]
    sample_name: str = re.sub(r"^([\w\-]+)_\d$", r"\1", first_qaccver)
    if first_qaccver == sample_name:
        sample_name = re.sub(r"^(.+)_[1-8]_\w{1,3}$", r"\1", first_qaccver)
    logger.info(
        f"{sample_name} | n={df_filtered.shape[0]} | Filtered for hits above {pident_threshold}% identity."
        f"and Min Alignment length > {min_aln_length}"
    )
    df_filtered = df_filtered.with_columns([
        pl.col('saccver').str.strip().alias("#Accession"),
        pl.lit(sample_name, dtype=pl.Categorical).alias("sample"),
        pl.col('qaccver').str.extract(f"{sample_name}_([1-8]).*").cast(pl.Categorical).alias("sample_segment"),
        pl.col("stitle").str.extract(regex_subtype_pattern).alias("subtype_from_match_title").cast(pl.Categorical)
    ])
    logger.info(
        f"{sample_name} | Merging NCBI Influenza DB genome metadata with BLAST results on accession."
    )
    df_merge = df_filtered.join(df_metadata, on="#Accession", how="left")
    del df_filtered
    del df_metadata
    df_merge = df_merge.with_columns(
        pl.when(pl.col("Genotype").is_null())
        .then(pl.col("subtype_from_match_title"))
        .otherwise(pl.col("Genotype"))
        .alias("Genotype")
    )
    # if sample_segment is all null, then try to get the segment number the top matching ref seqs
    if df_merge["sample_segment"].is_null().all():
        df_merge = df_merge.with_columns(
            pl.when(pl.col("sample_segment").is_null())
            .then(pl.col("Segment"))
            .otherwise(pl.col("sample_segment"))
            .alias("sample_segment")
        )

        df_merge = df_merge.filter(pl.col("sample_segment").is_not_null() & pl.col("sample_segment").is_in(["1", "2", "3", "4", "5", "6", "7", "8"]))


    df_merge = df_merge.sort(
        by=["sample_segment", "bitscore"], descending=[False, True]
    )

    segments = df_merge["sample_segment"].unique().sort()
    logger.debug(f"{segments=}")
    dfs = [
        df_merge.filter(pl.col("sample_segment") == seg).head(top)
        for seg in segments
    ]
    df_top_seg_matches = pl.concat(dfs, how="vertical")
    cols = pl.Series([x for x, _ in BLAST_RESULTS_REPORT_COLUMNS])
    df_top_seg_matches = df_top_seg_matches.select(pl.col(cols))
    subtype_results_summary = {"sample": sample_name}
    genus = top_genus(df_merge)
    if not get_top_ref:
        is_iav = genus == 'Alphainfluenzavirus'
        H_results = None
        N_results = None
        if "4" in segments:
            H_results = find_h_or_n_type(df_merge, "4", is_iav, min_pident=pident_threshold)
            subtype_results_summary |= H_results
        if "6" in segments:
            N_results = find_h_or_n_type(df_merge, "6", is_iav, min_pident=pident_threshold)
            subtype_results_summary.update(N_results)
        subtype_results_summary["Genotype"] = get_subtype_value(H_results, N_results, is_iav)

    return df_top_seg_matches, subtype_results_summary, genus


def top_genus(df: pl.DataFrame) -> str:
    genus: pl.Series = df['Genus']
    return genus.value_counts(sort=True)['Genus'][0]


def get_subtype_value(H_results: Optional[Dict], N_results: Optional[Dict], is_iav: bool) -> str:
    subtype = ""
    if not is_iav:
        return "N/A"
    if H_results is None and N_results is None:
        subtype = "-"
    elif H_results is not None and N_results is None:
        H: str = H_results.get("H_type", "")
        subtype = f"H{H}" if H != "" else "-"
    elif H_results is None:
        N: str = N_results.get("N_type", "")
        subtype = f"N{N}" if N != "" else "-"
    else:
        H: str = H_results.get("H_type", "")
        N: str = N_results.get("N_type", "")
        if H or N:
            if H != "":
                H = f"H{H}"
            if N != "":
                N = f"N{N}"
            subtype = f"{H}{N}"
        else:
            subtype = "-"
    return subtype


def find_h_or_n_type(
        df_merge: pl.DataFrame,
        seg: str,
        is_iav: bool,
        min_pident: float = 0.85,
) -> Dict[str, Union[str, int, float]]:
    assert seg in {
        "4",
        "6",
    }, "Can only determine H or N type from segments 4 or 6, respectively!"
    h_or_n, type_name = ("H", "H_type") if seg == "4" else ("N", "N_type")
    df_segment = df_merge.filter(pl.col("sample_segment") == seg)
    if df_segment.shape[0] == 0:
        return {
            f"{h_or_n}_type": "N/A",
            f"{h_or_n}_sample_segment_length": "N/A",
            f"{h_or_n}_top_pident": "N/A",
            f"{h_or_n}_top_mismatch": "N/A",
            f"{h_or_n}_top_gaps": "N/A",
            f"{h_or_n}_top_bitscore": "N/A",
            f"{h_or_n}_top_align_length": "N/A",
            f"{h_or_n}_top_accession": "N/A",
            f"{h_or_n}_top_host": "N/A",
            f"{h_or_n}_top_country": "N/A",
            f"{h_or_n}_top_date": "N/A",
            f"{h_or_n}_top_seq_length": "N/A",
            f"{h_or_n}_virus_name": "N/A",
            f"{h_or_n}_NCBI_Influenza_DB_subtype_matches": "N/A",
            f"{h_or_n}_NCBI_Influenza_DB_total_matches": "N/A",
            f"{h_or_n}_NCBI_Influenza_DB_proportion_matches": "N/A",
            f"{h_or_n}_NCBI_Influenza_DB_pident_threshold": "N/A",
        }
    reg_h_or_n_type = "[Hh]" if h_or_n == "H" else "[Nn]"
    df_segment = df_segment.with_columns(
        pl.col("Genotype").str.extract(reg_h_or_n_type + r"(\d+)").alias(type_name)
    )
    top_match = df_segment.head(1)
    top_match_query = top_match["qaccver"][0]
    top_match_pident = top_match["pident"][0]
    top_match_aln_length = top_match["length"][0]
    pident_threshold = None
    top_type = "N/A"
    top_type_count = "N/A"
    total_count = "N/A"
    if is_iav:
        # Low quality matches may lead to incorrect subtyping so we try to first filter for high quality matches
        # start at 99% identity and work down to minimum threshold by 1% increments
        for pident_threshold in range(99, int(min_pident * 100) - 1, -1):
            df_filt = df_segment.filter((pl.col("pident") >= pident_threshold) & ~(pl.col(type_name).is_null()))
            if df_filt.shape[0] == 0:
                continue

            if df_filt.shape[0] <= 2:
                logger.info(f'{top_match_query}| Segment {seg} top match at {top_match_pident}% identity with alignment length {top_match_aln_length}.')
                # if the rest of the hits are low alignment length, e.g. < 200, then use the top match and skip the rest
                df_filt_rest = df_segment.filter(
                    (pl.col("pident") < pident_threshold)
                    & ~(pl.col(type_name).is_null())
                    & (pl.col("length") > 200)
                )
                if df_filt_rest.shape[0] == 0:
                    df_type_counts = df_filt.filter(~pl.col(type_name).is_null())[type_name].value_counts(sort=True)
                    logger.info(f"{top_match_query}| {df_type_counts=}")
                    type_to_count = defaultdict(int)
                    for x in df_type_counts.iter_rows(named=True):
                        type_to_count[x[type_name]] += x["counts"]
                    type_to_count = list(type_to_count.items())
                    type_to_count.sort(key=lambda x: x[1], reverse=True)
                    if not type_to_count:
                        break
                    if len(type_to_count) > 1:
                        logger.warning(f'{top_match_query}| Segment {seg} top match at {top_match_pident}% identity with alignment length {top_match_aln_length} but multiple subtypes found: {type_to_count}.')
                    top_type = top_match[type_name][0]
                    logger.info(f'{top_match_query}| {type_to_count=} {top_match=} {top_type=}')
                    top_type_count = 1
                    total_count = df_filt.shape[0]
                    logger.info(
                        f"{top_match_query}| {h_or_n}{top_type} n={top_type_count}/{total_count} ({top_type_count / total_count:.1%}) at {pident_threshold}% identity."
                    )
                    break
            type_counts = df_filt[type_name].value_counts(sort=True)
            df_type_counts = type_counts.filter(~pl.col(type_name).is_null())
            logger.debug(f"{df_type_counts=}")
            type_to_count = defaultdict(int)
            for x in df_type_counts.iter_rows(named=True):
                type_to_count[x[type_name]] += x["counts"]
            type_to_count = list(type_to_count.items())
            type_to_count.sort(key=lambda x: x[1], reverse=True)
            logger.debug(f"{type_to_count=}")
            if not type_to_count:
                if pident_threshold >= min_pident * 100:
                    continue
                logger.info(f"No {h_or_n} type found at {pident_threshold}% identity.")
                break
            top_type, top_type_count = type_to_count[0]
            total_count = type_counts["counts"].sum()
            logger.info(
                f"{top_match_query}| {h_or_n}{top_type} n={top_type_count}/{total_count} ({top_type_count / total_count:.1%}) at {pident_threshold}% identity."
            )
            break

    top_result: Dict[str, Any] = list(df_segment.head(1).iter_rows(named=True))[0]
    db_prop_matches = top_type_count / total_count if is_iav and not isinstance(top_type_count, str) and not isinstance(total_count, str) else "N/A"
    results_summary = {
        f"{h_or_n}_type": top_type if is_iav else "N/A",
        f"{h_or_n}_sample_segment_length": top_result["qlen"],
        f"{h_or_n}_top_pident": top_result["pident"],
        f"{h_or_n}_top_mismatch": top_result["mismatch"],
        f"{h_or_n}_top_gaps": top_result["gapopen"],
        f"{h_or_n}_top_bitscore": top_result["bitscore"],
        f"{h_or_n}_top_align_length": top_result["length"],
        f"{h_or_n}_top_accession": top_result["#Accession"],
        f"{h_or_n}_top_host": top_result["Host"],
        f"{h_or_n}_top_country": top_result["Geo_Location"],
        f"{h_or_n}_top_date": top_result["Collection_Date"],
        f"{h_or_n}_top_seq_length": top_result["slen"],
        f"{h_or_n}_virus_name": top_result["GenBank_Title"],
        f"{h_or_n}_NCBI_Influenza_DB_subtype_matches": top_type_count,
        f"{h_or_n}_NCBI_Influenza_DB_total_matches": total_count,
        f"{h_or_n}_NCBI_Influenza_DB_proportion_matches": db_prop_matches,
        f"{h_or_n}_NCBI_Influenza_DB_pident_threshold": pident_threshold,
    }
    logger.info(f"Seg {seg} results: {results_summary}")
    return results_summary


def write_top_segment_matches(
        df_top_seg_matches: pl.DataFrame,
        sample_name: str,
        genus: str
):
    df_blast = df_top_seg_matches.rename(mapping=dict(BLAST_RESULTS_REPORT_COLUMNS))
    df_ref_id = df_blast.select(
        pl.col([
            'Sample',
            'Sample Genome Segment Number',
            'Reference NCBI Accession',
            'BLASTN Bitscore',
            'Reference Sequence ID'
        ])
    )
    df_ref_id = df_ref_id.with_columns(
        pl.when(pl.col("Reference NCBI Accession").is_null())
        .then(pl.col("Reference Sequence ID"))
        .otherwise(pl.col("Reference NCBI Accession"))
        .str.strip()
        .alias('Reference NCBI Accession')
    )
    segment_names = get_segment_names(genus)
    df_ref_id = df_ref_id.with_columns(
        pl.col("Sample Genome Segment Number").apply(lambda x: segment_names.get(int(x), x))
    )
    df_ref_id.write_csv(
        f"{sample_name}.topsegments.csv", separator=",", has_header=True
    )


def version_callback(value: bool):
    if value:
        typer.echo(f"{VERSION}")
        raise typer.Exit()


def get_segment_names(genus: str) -> Dict[int, str]:
    segment_names = {}
    if genus == 'Alphainfluenzavirus':
        segment_names = IAV_SEGMENT_NAMES
    elif genus == 'Betainfluenzavirus':
        segment_names = IBV_SEGMENT_NAMES
    return segment_names


def read_refseq_metadata(flu_metadata):
    return pl.read_csv(
        flu_metadata,
        has_header=True,
        dtypes=dict(METADATA_COLUMNS),
    )


def init_logging(verbose: bool = False) -> None:
    from rich.traceback import install
    install(show_locals=True, width=120, word_wrap=True)
    logging.basicConfig(
        format="%(message)s",
        datefmt="[%Y-%m-%d %X]",
        level=logging.DEBUG if verbose else logging.INFO,
        handlers=[RichHandler(rich_tracebacks=True, tracebacks_show_locals=False)],
    )


def get_col_widths(df, index=False):
    """Calculate column widths based on column headers and contents"""
    if index:
        yield max(
            [len(str(s)) for s in df.index.values] + [len(str(df.index.name))]
        )
    for c in df.columns:
        # get max length of column contents and length of column header
        yield np.max([df[c].astype(str).str.len().max() + 1, len(c) + 1])


def write_excel(
        name_dfs: List[Tuple[str, pd.DataFrame]],
        output_dest: PathLike,
        sheet_name_index: bool = True,
) -> None:
    logger.info("Starting to write tabular data to worksheets in Excel workbook")
    with pd.ExcelWriter(output_dest, engine="xlsxwriter") as writer:
        idx = 1
        for name_df in name_dfs:
            if not isinstance(name_df, (list, tuple)):
                logger.error(
                    'Input "%s" is not a list or tuple (type="%s"). Skipping...',
                    name_df,
                    type(name_df),
                )
                continue
            sheetname, df = name_df
            fixed_sheetname = REGEX_UNALLOWED_EXCEL_WS_CHARS.sub("_", sheetname)
            # fixed max number of characters in sheet name due to compatibility
            if sheet_name_index:
                max_chars = 28
                fixed_sheetname = f"{idx}_{fixed_sheetname[:max_chars]}"
            else:
                max_chars = 31
                fixed_sheetname = fixed_sheetname[:max_chars]

            if len(fixed_sheetname) > max_chars:
                logger.warning(
                    'Sheetname "%s" is >= %s characters so may be truncated (n=%s)',
                    max_chars,
                    fixed_sheetname,
                    len(fixed_sheetname),
                )

            logger.info(f'Writing table to Excel sheet "{fixed_sheetname}"')
            df.to_excel(
                writer, sheet_name=fixed_sheetname, index=False, freeze_panes=(1, 1)
            )
            worksheet = writer.book.get_worksheet_by_name(fixed_sheetname)
            for i, width in enumerate(get_col_widths(df, index=False)):
                worksheet.set_column(i, i, width)
            idx += 1
    logger.info('Done writing worksheets to spreadsheet "%s".', output_dest)

def get_vadr_mdl_subtype(mdl_path: Path):
    vadr_h_subtype = ""
    vadr_n_subtype = ""
    with mdl_path.open() as f:
        first_line = f.readline()
        col_line = f.readline()
        cols = col_line.strip().split()
        for line in f:
            if line.startswith("#"):
                continue
            line_dict = dict(zip(cols, line.strip().split()))
            if line_dict["group"] == "fluA-seg4":
                vadr_h_subtype = line_dict["subgroup"]
            if line_dict["group"] == "fluA-seg6":
                vadr_n_subtype = line_dict["subgroup"]
    return vadr_h_subtype, vadr_n_subtype

def find_matching_files(
        directory: Path,
        pattern: str,
        recursive: bool = True,
        ignore_case: bool = True,
) -> List[Path]:
    # Walk through the directory recursively, following symlinks
    matching_files = []
    for root, dirs, files in os.walk(directory, followlinks=True):
        for file_name in files:
            if file_name.endswith(pattern):
                matching_files.append(Path(root) / file_name)
    return matching_files

@app.command()
def report(
        flu_metadata: Path = typer.Option(
            ..., "--flu-metadata", "-m",
            help="NCBI Influenza metadata tab-delimited TSV file"
        ),
        excel_report: Path = typer.Option(Path("nf-flu-subtyping-report.xlsx"), help="Excel report output path"),
        outdir: Path = typer.Option(Path("subtyping_report"), help="Output directory"),
        top: int = typer.Option(5, help="Top N matches to each segment to report"),
        pident_threshold: float = typer.Option(0.85, help="BLAST percent identity threshold"),
        min_aln_length: int = typer.Option(50, help="Min BLAST alignment length threshold"),
        get_top_ref: bool = typer.Option(False, is_flag=True, help="Get top ref accession id from ncbi database."),
        sample_name: str = typer.Option("", help="Sample Name."),
        samplesheet: Path = typer.Option(None, help="samplesheet.csv to get order of samples."),
        vadr_mdl_dir: Path = typer.Option(None, help="Directory with VADR .mdl files."),
        verbose: bool = typer.Option(False, "--verbose", is_flag=True, help="Enable verbose logging"),
        version: bool = typer.Option(None, "--version", callback=version_callback, is_eager=True, is_flag=True),
        input_blast_results_dir: Path = typer.Option(..., "-i", "--input-blast-results-dir",
                                                       dir_okay=True, help="Directory with BLAST results files")
):
    init_logging(verbose)
    logger.info(f"nf-flu {__name__} version {VERSION}")
    logger.info(f"Starting subtyping report generation with parameters: {locals()}")
    blast_results: list[Path] = list(input_blast_results_dir.glob("*.blastn.txt"))
    logger.debug(f"{blast_results=}")
    if not blast_results:
        logger.error(f"No BLAST results files found in directory '{blast_results}'!")
        sys.exit(1)
    ordered_samples: Optional[List[str]] = None
    if samplesheet:
        samplesheet_path = Path(samplesheet)
        if samplesheet_path.resolve().exists():
            # force reading of samplesheet.csv columns as string data type
            ordered_samples = pl.read_csv(samplesheet_path, dtypes=[pl.Utf8, pl.Utf8])['sample'].to_list()
            logger.info(f"Using samplesheet to order samples: {ordered_samples}")

    vadr_sample_subtype = {}
    if vadr_mdl_dir is not None:
        logger.info(f"Reading VADR .mdl files from directory: {vadr_mdl_dir}")
        mdl_paths = find_matching_files(vadr_mdl_dir, ".mdl")
        sample_to_mdl = {p.name.replace(".vadr.mdl", ""): p for p in mdl_paths}
        logger.info(f"Found {len(sample_to_mdl)} VADR .mdl files.")
        for sample, mdl_path in sample_to_mdl.items():
            vadr_h_subtype, vadr_n_subtype = get_vadr_mdl_subtype(mdl_path)
            logger.info(f"{sample}: VADR H subtype: {vadr_h_subtype}, N subtype: {vadr_n_subtype}")
            vadr_sample_subtype[sample] = f"{vadr_h_subtype}{vadr_n_subtype}"

    logger.debug(f"{vadr_sample_subtype=}")
    logger.info(f'Parsing Influenza metadata file "{flu_metadata}"')

    df_md = read_refseq_metadata(flu_metadata)

    unique_subtypes = df_md.select("Genotype").unique()
    unique_subtypes = unique_subtypes.filter(~pl.col("Genotype").is_null())
    logger.info(
        f"Parsed Influenza metadata file into DataFrame with n={df_md.shape[0]} rows and n={df_md.shape[1]} columns. "
        f"There are {len(unique_subtypes)} unique subtypes."
    )
    regex_subtype_pattern = r"\((H\d+N\d+|" + "|".join(list(unique_subtypes["Genotype"])) + r")\)"
    results = [
        parse_blast_result(
            blast_result,
            df_md,
            regex_subtype_pattern,
            get_top_ref,
            top=top,
            pident_threshold=pident_threshold,
            min_aln_length=min_aln_length
        )
        for blast_result in blast_results
    ]

    if get_top_ref:
        df_top_seg_matches, _, genus = results[0]
        write_top_segment_matches(df_top_seg_matches, sample_name, genus)
    else:
        dfs_blast = []
        all_subtype_results = {}
        for parsed_result in results:
            if parsed_result is None:
                continue
            df_blast, subtype_results_summary, genus = parsed_result
            if df_blast is not None:
                # Replace segment number with segment name, i.e. 1 with "1_PB2" for IAV, 1 with "1_PB1" for IBV.
                # No replacement for all other genera.
                segment_names = get_segment_names(genus)
                if df_blast["sample_segment"].is_null().all():
                    logger.info(f"No segment information found for {subtype_results_summary['sample']}.")

                df_blast = df_blast.with_columns(pl.col("sample_segment").apply(lambda x: segment_names.get(int(x), x)))
                dfs_blast.append(df_blast)
            sample = subtype_results_summary["sample"]
            all_subtype_results[sample] = subtype_results_summary
        df_all_blast = pl.concat(dfs_blast, how='vertical')
        df_subtype_results = pd.DataFrame(all_subtype_results).transpose()
        ordered_sample_to_idx = {sample: idx for idx, sample in enumerate(ordered_samples)} if ordered_samples else None

        cols_concat = {}
        for col in SUBTYPE_RESULTS_SUMMARY_COLUMNS + H_COLUMNS + N_COLUMNS:
            if col in df_subtype_results.columns:
                cols_concat[col] = ''
        df_subtype_results = df_subtype_results[list(cols_concat.keys())]

        if ordered_samples and ordered_sample_to_idx:
            df_subtype_results = df_subtype_results.sort_values(
                'sample',
                ascending=True,
                key=lambda x: x.map(ordered_sample_to_idx)
            )
        else:
            df_subtype_results = df_subtype_results.sort_values('sample', ascending=True)
        cols = pd.Series(H_COLUMNS)
        cols = cols[cols.isin(df_subtype_results.columns)]
        df_H = df_subtype_results[cols]
        cols = pd.Series(N_COLUMNS)
        cols = cols[cols.isin(df_subtype_results.columns)]
        df_N = df_subtype_results[cols]

        # cast all categorical columns to string so that they can be sorted/ordered in a sensible way
        df_all_blast = df_all_blast.with_columns(
            pl.col(pl.Categorical).cast(pl.Utf8),
        )
        # apply custom sort order to sample column if samplesheet is provided
        sample_col = pl.col('sample').map_dict(
            ordered_sample_to_idx) if ordered_samples and ordered_sample_to_idx else pl.col('sample')
        # Sort by sample names, segment numbers and bitscore
        df_all_blast = df_all_blast.sort(
            sample_col,
            pl.col('sample_segment'),
            pl.col('bitscore'),
            descending=[False, False, True]
        )
        df_all_blast_pandas: pd.DataFrame = df_all_blast.to_pandas()
        # Rename columns to more human-readable names
        df_all_blast_pandas = df_all_blast_pandas.rename(
            columns=dict(BLAST_RESULTS_REPORT_COLUMNS)
        )
        df_subtype_predictions = df_subtype_results[SUBTYPE_RESULTS_SUMMARY_COLUMNS]
        if vadr_sample_subtype:
            df_subtype_predictions['VADR Subtype'] = df_subtype_predictions['sample'].map(vadr_sample_subtype)
            cols = [*SUBTYPE_RESULTS_SUMMARY_COLUMNS]
            cols.insert(2, 'VADR Subtype')
            df_subtype_predictions = df_subtype_predictions[cols]
        df_subtype_predictions = df_subtype_predictions.rename(columns=SUBTYPE_RESULTS_SUMMARY_FINAL_NAMES)
        df_H = df_H.rename(columns=SUBTYPE_RESULTS_SUMMARY_FINAL_NAMES)
        df_N = df_N.rename(columns=SUBTYPE_RESULTS_SUMMARY_FINAL_NAMES)
        # Write each dataframe to a separate sheet in the excel report
        write_excel(
            [
                ("Subtype Predictions", df_subtype_predictions),
                ("Top Segment Matches", df_all_blast_pandas),
                ("H Segment Results", df_H),
                ("N Segment Results", df_N),
            ],
            output_dest=excel_report,
        )
        if not outdir.exists():
            outdir.mkdir(parents=True)
        df_all_blast.write_csv(outdir / "all_blast_results.csv")
        df_subtype_results.to_csv(outdir / "subtype_results.csv")
        df_subtype_predictions.to_csv(outdir / "subtype_predictions.csv")
        df_H.to_csv(outdir / "H_segment_results.csv")
        df_N.to_csv(outdir / "N_segment_results.csv")
        logger.info(f"Results written to {outdir}")


if __name__ == "__main__":
    app()
