import os
import boto3
from flask import Flask, render_template, request, redirect, url_for, flash, session
from botocore.exceptions import ClientError, NoCredentialsError
import pytz

# 指定 templates 相对路径，确保在 Vercel Serverless 环境下能正确读取
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
template_dir = os.path.join(base_dir, 'templates')

app = Flask(__name__, template_folder=template_dir)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'supersecretkey_change_in_production')


def get_s3_client():
    """获取 S3 客户端，优先使用环境变量，其次使用 Session 中的配置"""
    aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID') or session.get('aws_access_key_id')
    aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY') or session.get('aws_secret_access_key')
    region_name = os.environ.get('AWS_DEFAULT_REGION') or session.get('region_name', 'us-east-1')
    endpoint_url = os.environ.get('S3_ENDPOINT_URL') or session.get('endpoint_url')

    if not aws_access_key or not aws_secret_key:
        return None

    kwargs = {
        'aws_access_key_id': aws_access_key,
        'aws_secret_access_key': aws_secret_key,
        'region_name': region_name
    }
    if endpoint_url:
        kwargs['endpoint_url'] = endpoint_url

    return boto3.client('s3', **kwargs)


@app.route('/')
def index():
    s3 = get_s3_client()
    if not s3:
        return redirect(url_for('configure'))

    try:
        response = s3.list_buckets()
        buckets = response.get('Buckets', [])
        return render_template('buckets.html', buckets=buckets)
    except (ClientError, NoCredentialsError) as e:
        flash(f"凭证校验失败或连接错误: {str(e)}", "danger")
        return redirect(url_for('configure'))


@app.route('/configure', methods=['GET', 'POST'])
def configure():
    if request.method == 'POST':
        session['aws_access_key_id'] = request.form.get('aws_access_key_id', '').strip()
        session['aws_secret_access_key'] = request.form.get('aws_secret_access_key', '').strip()
        session['region_name'] = request.form.get('region_name', 'us-east-1').strip()
        session['endpoint_url'] = request.form.get('endpoint_url', '').strip() or None

        flash("S3 凭证已保存至 Session", "success")
        return redirect(url_for('index'))

    return render_template('configure.html')


@app.route('/bucket/<bucket_name>')
def bucket_contents(bucket_name):
    s3 = get_s3_client()
    if not s3:
        return redirect(url_for('configure'))

    prefix = request.args.get('prefix', '')
    try:
        paginator = s3.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=prefix, Delimiter='/')

        folders = []
        objects = []

        for page in page_iterator:
            if 'CommonPrefixes' in page:
                for p in page['CommonPrefixes']:
                    folders.append(p['Prefix'])

            if 'Contents' in page:
                for obj in page['Contents']:
                    if obj['Key'] != prefix:
                        objects.append(obj)

        return render_template(
            'bucket_contents.html',
            bucket_name=bucket_name,
            prefix=prefix,
            folders=folders,
            objects=objects
        )
    except ClientError as e:
        flash(f"读取 Bucket 内容失败: {str(e)}", "danger")
        return redirect(url_for('index'))


@app.route('/search', methods=['GET', 'POST'])
def search():
    s3 = get_s3_client()
    if not s3:
        return redirect(url_for('configure'))

    if request.method == 'POST':
        bucket_name = request.form.get('bucket_name')
        query = request.form.get('query', '').strip()

        if not bucket_name or not query:
            flash("请提供 Bucket 名称和搜索关键词", "warning")
            return redirect(url_for('search'))

        try:
            paginator = s3.get_paginator('list_objects_v2')
            page_iterator = paginator.paginate(Bucket=bucket_name)

            results = []
            for page in page_iterator:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        if query.lower() in obj['Key'].lower():
                            results.append(obj)

            return render_template(
                'search_results.html',
                bucket_name=bucket_name,
                query=query,
                results=results
            )
        except ClientError as e:
            flash(f"搜索失败: {str(e)}", "danger")
            return redirect(url_for('search'))

    return render_template('search.html')


# Vercel Serverless 环境暴露 app 变量供 Entrypoint 识别
