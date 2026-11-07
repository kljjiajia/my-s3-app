import os
import boto3
from flask import Flask, render_template, request, redirect, url_for, flash, session
from botocore.exceptions import ClientError, NoCredentialsError

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
template_dir = os.path.join(base_dir, 'templates')

app = Flask(__name__, template_folder=template_dir)
# 确保包含默认 Key，即使 Vercel 没配置环境变量也不会导致 Session 失效
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'default_fallback_secret_key_123456')

def get_s3_client():
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
        # 如果获取不到 S3 客户端，强制带上原因跳转
        flash("未检测到有效的 S3 凭证，请先配置", "warning")
        return redirect(url_for('configure'))

    try:
        response = s3.list_buckets()
        buckets = response.get('Buckets', [])
        return render_template('buckets.html', buckets=buckets)
    except (ClientError, NoCredentialsError) as e:
        flash(f"S3 连接失败: {str(e)}", "danger")
        return redirect(url_for('configure'))
    except Exception as e:
        flash(f"发生未知错误: {str(e)}", "danger")
        return redirect(url_for('configure'))


@app.route('/configure', methods=['GET', 'POST'])
def configure():
    if request.method == 'POST':
        # 1. 读取表单数据并存入 Session
        session['aws_access_key_id'] = request.form.get('aws_access_key_id', '').strip()
        session['aws_secret_access_key'] = request.form.get('aws_secret_access_key', '').strip()
        session['region_name'] = request.form.get('region_name', 'us-east-1').strip()
        
        endpoint = request.form.get('endpoint_url', '').strip()
        session['endpoint_url'] = endpoint if endpoint else None

        # 2. 显式标记 Session 已修改（解决 Vercel Serverless 环境下的 Cookie 丢失问题）
        session.modified = True

        # 3. 校验填写的凭证
        s3 = get_s3_client()
        if not s3:
            flash("请输入完整的 Access Key 和 Secret Key！", "danger")
            return render_template('configure.html')

        try:
            # 尝试发起一次网络请求，检验凭证是否真实可用
            s3.list_buckets()
            flash("S3 连接成功！", "success")
            return redirect(url_for('index'))
        except Exception as e:
            flash(f"凭证校验失败: {str(e)}", "danger")
            # 校验失败时留在本页，并把错误直接显示出来
            return render_template('configure.html')

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

        return render_template('bucket_contents.html', bucket_name=bucket_name, prefix=prefix, folders=folders, objects=objects)
    except Exception as e:
        flash(f"获取 Bucket 内容失败: {str(e)}", "danger")
        return redirect(url_for('index'))


@app.route('/search', methods=['GET', 'POST'])
def search():
    s3 = get_s3_client()
    if not s3:
        return redirect(url_for('configure'))

    if request.method == 'POST':
        bucket_name = request.form.get('bucket_name')
        query = request.form.get('query', '').strip()
        try:
            paginator = s3.get_paginator('list_objects_v2')
            page_iterator = paginator.paginate(Bucket=bucket_name)

            results = []
            for page in page_iterator:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        if query.lower() in obj['Key'].lower():
                            results.append(obj)
            return render_template('search_results.html', bucket_name=bucket_name, query=query, results=results)
        except Exception as e:
            flash(f"搜索失败: {str(e)}", "danger")
            return redirect(url_for('search'))

    return render_template('search.html')
