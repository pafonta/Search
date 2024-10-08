# Blue Brain Search is a text mining toolbox focused on scientific use cases.
#
# Copyright (C) 2020  Blue Brain Project, EPFL.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
"""Utils for journal/articles topics."""
from __future__ import annotations

import html
import logging
import pathlib
from functools import lru_cache
from typing import Iterable, List
from xml.etree.ElementTree import Element  # nosec

import requests
from defusedxml import ElementTree

from bluesearch.database.article import JATSXMLParser

logger = logging.getLogger(__name__)


# Journal Topic
@lru_cache(maxsize=None)
def request_mesh_from_nlm_ta(nlm_ta: str) -> list[dict] | None:
    """Retrieve Medical Subject Heading from Journal's NLM Title Abbreviation.

    Parameters
    ----------
    nlm_ta
        NLM Title Abbreviation of Journal.

    Returns
    -------
    meshs
        List containing all meshs of the Journal.

    References
    ----------
    https://www.ncbi.nlm.nih.gov/books/NBK3799/#catalog.Title_Abbreviation_ta
    """
    if "&" in nlm_ta:
        logger.error(
            "Ampersands not allowed in the NLM title abbreviation. "
            f"Try unescaping HTML characters first. Got:\n{nlm_ta}"
        )
        return None

    # The "format=text" parameter only matters when no result was found. With
    # this parameter the returned text will be an empty string. See the
    # corresponding check further below. Without this parameter the output is
    # an HTML page, which is impossible to parse.
    base_url = "https://www.ncbi.nlm.nih.gov/nlmcatalog"
    params = {"term": f'"{nlm_ta}"[ta]', "report": "xml", "format": "text"}

    response = requests.get(base_url, params=params)
    response.raise_for_status()

    # The way NCBI responds to these queries is weird: it takes the XML file,
    # escapes all XML tags and wraps it into a pair of <pre> tag inside an HTML
    # response with a fixed header
    # So we need to check if the response is in exactly this form, strip away the
    # HTML part, and unescape the XML tags.
    text = response.text.strip()
    header = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" '
        '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">\n'
        "<pre>"
    )
    footer = "</pre>"
    if not text.startswith(header) or not text.endswith(footer):
        logger.error(f"Unexpected response for parameters \n{params}")
        return None
    text = html.unescape(text[len(header) - 5 :]).strip()

    # Empty text means topic abbreviation was not found. See comment about the
    # parameter "format=text" above.
    if text == "<pre></pre>":
        logger.error(f"Empty body for parameters \n{params}")
        return None

    content = ElementTree.fromstring(text)
    mesh_headings = content.findall(
        "./NCBICatalogRecord/NLMCatalogRecord/MeshHeadingList/MeshHeading"
    )
    return _parse_mesh_from_nlm_catalog(mesh_headings)


# Article Topic
def request_mesh_from_pubmed_id(pubmed_ids: Iterable[str]) -> dict:
    """Retrieve Medical Subject Headings from Pubmed ID.

    Parameters
    ----------
    pubmed_ids
        List of Pubmed IDs.

    Returns
    -------
    pubmed_to_meshs : dict
        Dictionary containing Pubmed IDs as keys with corresponding
        Medical Subject Headings list as values.

    References
    ----------
    https://dataguide.nlm.nih.gov/eutilities/utilities.html#efetch
    """
    pubmed_str = ",".join(pubmed_ids)
    url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"
        f"db=pubmed&id={pubmed_str}&retmode=xml"
    )
    response = requests.get(url)

    if not response.ok:
        response.raise_for_status()

    content = ElementTree.fromstring(response.content.decode())
    pubmed_articles = content.findall("./PubmedArticle")
    pubmed_to_meshs = {}

    for article in pubmed_articles:
        pubmed_id_tag = article.find(
            "./PubmedData/ArticleIdList/ArticleId[@IdType='pubmed']"
        )
        if pubmed_id_tag is None:
            continue
        pubmed_id = pubmed_id_tag.text
        mesh_headings = article.findall("./MedlineCitation/MeshHeadingList")
        meshs = _parse_mesh_from_pubmed(mesh_headings)
        pubmed_to_meshs[pubmed_id] = meshs

    return pubmed_to_meshs


# Utils
def extract_pubmed_id_from_pmc_file(path: str | pathlib.Path) -> str | None:
    """Retrieve Pubmed ID from PMC XML file.

    Parameters
    ----------
    path
        Path to PMC XML.

    Returns
    -------
    pubmed_id : str
        Pubmed ID of the given article
    """
    content = ElementTree.parse(path)
    pmid_tag = content.find("./front/article-meta/article-id[@pub-id-type='pmid']")
    if pmid_tag is None:
        return None
    else:
        return pmid_tag.text


def _parse_mesh_from_nlm_catalog(mesh_headings: Iterable[Element]) -> list[dict]:
    """Retrieve Medical Subject Headings from nlmcatalog parsing.

    Parameters
    ----------
    mesh_headings
        XML parsing element containing all Medical Subject Headings.

    Returns
    -------
    mesh : list of dict
        List of dictionary containing Medical Subject Headings information.
    """
    meshs = []
    for mesh in mesh_headings:

        mesh_id = mesh.attrib.get("URI", None)
        if mesh_id is not None:
            *_, mesh_id = mesh_id.rpartition("/")

        descriptor_name = []
        qualifier_name = []

        for elem in mesh:
            major_topic = elem.get("MajorTopicYN") == "Y"

            name = elem.text
            if name is not None:
                name = html.unescape(name)

            if elem.tag == "DescriptorName":
                descriptor_name.append(
                    {"name": name, "major_topic": major_topic, "ID": mesh_id}
                )
            else:
                qualifier_name.append({"name": name, "major_topic": major_topic})

        meshs.append({"descriptor": descriptor_name, "qualifiers": qualifier_name})

    return meshs


def _parse_mesh_from_pubmed(mesh_headings: Iterable[Element]) -> list[dict]:
    """Retrieve Medical Subject Headings from efetch pubmed parsing.

    Parameters
    ----------
    mesh_headings
        XML parsing element containing all Medical Subject Headings.

    Returns
    -------
    mesh : list of dict
        List of dictionary containing Medical Subject Headings information.
    """
    meshs = []

    for mesh_heading in mesh_headings:

        for mesh in list(mesh_heading):

            descriptor_name = []
            qualifiers_name = []

            for info in list(mesh):

                attributes = info.attrib

                mesh_id = attributes.get("UI", None)
                if mesh_id is not None:
                    *_, mesh_id = mesh_id.rpartition("/")

                major_topic = None
                if "MajorTopicYN" in attributes:
                    major_topic = attributes["MajorTopicYN"] == "Y"

                if info.tag == "DescriptorName":
                    descriptor_name.append(
                        {"ID": mesh_id, "major_topic": major_topic, "name": info.text}
                    )
                else:
                    qualifiers_name.append(
                        {"ID": mesh_id, "major_topic": major_topic, "name": info.text}
                    )

            meshs.append({"descriptor": descriptor_name, "qualifiers": qualifiers_name})

    return meshs


# PMC source
def get_topics_for_pmc_article(
    pmc_path: pathlib.Path | str,
) -> List[str] | None:
    """Extract journal topics of a PMC article.

    Parameters
    ----------
    pmc_path
        Path to the PMC article to consider

    Returns
    -------
    journal_topics : Optional[list[str]]
        Journal topics for the given article.
    """
    # Determine journal title
    parser = JATSXMLParser(pmc_path)
    nlm_ta = parser.content.find(
        "./front/journal-meta/journal-id[@journal-id-type='nlm-ta']"
    )
    if nlm_ta is None:
        logger.error(f"No NLM Title Abbreviation found for {pmc_path}")
        return None

    nlm_ta = nlm_ta.text
    logger.info(f"Journal Title Abbreviation: {nlm_ta}")
    journal_meshes = request_mesh_from_nlm_ta(nlm_ta)
    if journal_meshes is None:
        return None

    journal_topics = []
    for mesh in journal_meshes:
        for descriptor in mesh["descriptor"]:
            journal_topics.append(descriptor["name"])

    return journal_topics
