# Copyright 2019 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License").
# You may not use this file except in compliance with the License.
# A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file.
# This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, express or implied.
# See the License for the specific language governing permissions and limitations under the License.
import datetime
import json
import os
import re
import time

import boto3
from junitparser import JUnitXml

import untangle


def generate_cw_report(test_results_dir, namespace, aws_region):
    """
    Publish tests results to CloudWatch
    :param test_results_dir: dir containing the tests outputs.
    :param namespace: namespace for the CW metric.
    :param aws_region: region where to push the metric.
    """
    test_report_file = os.path.join(test_results_dir, "test_report.xml")
    if not os.path.isfile(test_report_file):
        generate_junitxml_merged_report(test_results_dir)
    report = generate_json_report(test_results_dir=test_results_dir, save_to_file=False)
    cw_client = boto3.client("cloudwatch", region_name=aws_region)
    timestamp = datetime.datetime.utcnow()

    for key, value in report.items():
        if key == "all":
            _put_metrics(cw_client, namespace, value, [], timestamp)
        else:
            for dimension_value, metrics in value.items():
                dimensions = [{"Name": key, "Value": dimension_value}]
                _put_metrics(cw_client, namespace, metrics, dimensions, timestamp)


def generate_junitxml_merged_report(test_results_dir):
    """
    Merge all junitxml generated reports in a single one.
    :param test_results_dir: output dir containing the junitxml reports to merge.
    """
    merged_xml = None
    for dir, _, files in os.walk(test_results_dir):
        for file in files:
            if file.endswith("results.xml"):
                if not merged_xml:
                    merged_xml = JUnitXml.fromfile(os.path.join(dir, file))
                else:
                    merged_xml += JUnitXml.fromfile(os.path.join(dir, file))

    merged_xml.write("{0}/test_report.xml".format(test_results_dir), pretty=True)


def generate_json_report(test_results_dir, save_to_file=True):
    """
    Generate a json report containing a summary of the tests results with details
    for each dimension.
    :param test_results_dir: dir containing the tests outputs.
    :param save_to_file:  True to save to file
    :return: a dictionary containing the computed report.
    """
    test_report_file = os.path.join(test_results_dir, "test_report.xml")
    if not os.path.isfile(test_report_file):
        generate_junitxml_merged_report(test_results_dir)

    result_to_label_mapping = {"skipped": "skipped", "failure": "failures", "error": "errors"}
    results = {"all": _empty_results_dict()}
    xml = untangle.parse(test_report_file)
    for testcase in xml.testsuite.children:
        label = "succeeded"
        for key, value in result_to_label_mapping.items():
            if hasattr(testcase, key):
                label = value
                break
        results["all"][label] += 1
        results["all"]["total"] += 1

        if hasattr(testcase, "properties"):
            for property in testcase.properties.children:
                _record_result(results, property["name"], property["value"], label)

        feature = re.sub(r"test_|_test|.py", "", os.path.splitext(os.path.basename(testcase["file"]))[0])
        _record_result(results, "feature", feature, label)

    if save_to_file:
        with open("{0}/test_report.json".format(test_results_dir), "w") as out_f:
            out_f.write(json.dumps(results, indent=4))

    return results


def _record_result(results_dict, dimension, dimension_value, label):
    if dimension not in results_dict:
        results_dict[dimension] = {}
    if dimension_value not in results_dict[dimension]:
        results_dict[dimension].update({dimension_value: _empty_results_dict()})
    results_dict[dimension][dimension_value][label] += 1
    results_dict[dimension][dimension_value]["total"] += 1


def _empty_results_dict():
    return {"total": 0, "skipped": 0, "failures": 0, "errors": 0, "succeeded": 0}


def _put_metrics(cw_client, namespace, metrics, dimensions, timestamp):
    for key, value in metrics.items():
        cw_client.put_metric_data(
            Namespace=namespace,
            MetricData=[
                {"MetricName": key, "Dimensions": dimensions, "Timestamp": timestamp, "Value": value, "Unit": "Count"}
            ],
        )
    failure_rate = float(metrics["failures"] + metrics["errors"]) / metrics["total"] * 100
    cw_client.put_metric_data(
        Namespace=namespace,
        MetricData=[
            {
                "MetricName": "failure_rate",
                "Dimensions": dimensions,
                "Timestamp": timestamp,
                "Value": failure_rate,
                "Unit": "Percent",
            }
        ],
    )
    # CloudWatch PutMetric API has a TPS of 150. Sleeping for 0.1 seconds so to publish 60 metrics per second
    time.sleep(0.1)
