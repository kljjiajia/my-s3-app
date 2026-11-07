# File: api/index.py  (Vercel Serverless 版本)
import os
from functools import lru_cache
from datetime import datetime

import boto3
import pytz
from botocore.config import Config
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, '..', 'templates')


# ----------------------------------------------------------------------
# 工具函数（与原版相同）
# ----------------------------------------------------------------------
def format_file_size(size_bytes):
    """格式化文件大小显示"""
    if size_bytes == 0:
        return "0 B"
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.2f} {size_names[i]}"


def convert_to_local_time(utc_time):
    """将UTC时间转换为本地时间（中国时区）"""
    if isinstance(utc_time, str):
        utc_time = datetime.fromisoformat(utc_time.replace('Z', '+00:00'))
    if utc_time.tzinfo is None:
        utc_time = pytz.UTC.localize(utc_time)
    return utc_time.astimezone(pytz.timezone('Asia/Shanghai'))


def sort_items(items, sort_by='date', sort_order='desc', item_type='file'):
    """排序文件或文件夹列表"""
    if not items:
        return items
    reverse = (sort_order == 'desc')
    try:
        if item_type == 'folder':
            if sort_by == 'name':
                return sorted(items, key=lambda x: x.get('name', '').lower(), reverse=reverse)
            return sorted(items, key=lambda x: x.get('path', '').lower(), reverse=reverse)

        if sort_by == 'name':
            return sorted(items, key=lambda x: x.get('name', '').lower(), reverse=reverse)
        elif sort_by == 'size':
            return sorted(items, key=lambda x: x.get('size', 0), reverse=reverse)
        elif sort_by == 'date':
            def get_date(x):
                date_val = x.get('last_modified')
                if date_val is None:
                    return datetime.min.replace(tzinfo=pytz.UTC)
                if isinstance(date_val, datetime):
                    return pytz.UTC.localize(date_val) if date_val.tzinfo is None else date_val
                try:
                    return datetime.fromisoformat(str(date_val).replace('Z', '+00:00'))
                except Exception:
                    return datetime.min.replace(tzinfo=pytz.UTC)
            return sorted(items, key=get_date, reverse=reverse)
        return sorted(items, key=lambda x: x.get('name', '').lower(), reverse=reverse)
    except Exception as e:
        print(f"排序错误: {str(e)}")
        return items


# ----------------------------------------------------------------------
# Flask 应用
# ----------------------------------------------------------------------
app = Flask(__name__, template_folder=TEMPLATE_DIR)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-change-this')
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('VERCEL') == '1',  # Vercel 上强制 HTTPS Cookie
)
app.jinja_env.globals.update(min=min, max=max)


@app.template_filter('to_local_time')
def to_local_time_filter(utc_time):
    """Jinja2过滤器：将UTC时间转换为本地时间"""
    return convert_to_local_time(utc_time)


# ----------------------------------------------------------------------
# S3 客户端：不再使用全局变量，每个请求从 session 恢复
# ----------------------------------------------------------------------
BOTO_CONFIG = Config(
    connect_timeout=5,
    read_timeout=25,
    retries={'max_attempts': 2, 'mode': 'standard'},
)


def create_s3_client(access_key, secret_key, region='us-east-1', endpoint_url=None):
    """创建 S3 客户端"""
    try:
        session_boto = boto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        if endpoint_url:
            return session_boto.client('s3', endpoint_url=endpoint_url, config=BOTO_CONFIG)
        return session_boto.client('s3', config=BOTO_CONFIG)
    except Exception as e:
        raise Exception(f"创建 S3 客户端失败: {str(e)}")


@lru_cache(maxsize=16)
def _cached_client(access_key, secret_key, region, endpoint_url):
    """同一个热实例内复用客户端，减少冷启动开销"""
    return create_s3_client(access_key, secret_key, region, endpoint_url or None)


def get_s3_client():
    """从当前 session 取得 S3 客户端；未配置返回 None"""
    if not session.get('aws_configured'):
        return None
    cfg = session.get('aws_config') or {}
    if not cfg.get('access_key') or not cfg.get('secret_key'):
        return None
    try:
        return _cached_client(
            cfg['access_key'],
            cfg['secret_key'],
            cfg.get('region') or 'us-east-1',
            cfg.get('endpoint_url') or '',
        )
    except Exception:
        return None


def test_s3_connection(client):
    """测试 S3 连接"""
    try:
        client.list_buckets()
        return True
    except Exception as e:
        raise Exception(f"S3 连接测试失败: {str(e)}")


# ----------------------------------------------------------------------
# 页面路由
# ----------------------------------------------------------------------
@app.route('/favicon.ico')
def favicon():
    # 避免浏览器请求 favicon 触发 404 处理器并闪现“页面未找到”
    return '', 204


@app.route('/')
def index():
    """主页 - 检查是否已配置"""
    if not session.get('aws_configured'):
        return redirect(url_for('configure'))
    return redirect(url_for('buckets'))


@app.route('/configure', methods=['GET', 'POST'])
def configure():
    """AWS 配置页面"""
    if request.method == 'POST':
        access_key = request.form.get('access_key', '').strip()
        secret_key = request.form.get('secret_key', '').strip()
        region = request.form.get('region', 'us-east-1').strip() or 'us-east-1'
        endpoint_url = request.form.get('endpoint_url', '').strip()

        if not access_key or not secret_key:
            flash('请输入 AWS Access Key ID 和 Secret Access Key', 'error')
            return render_template('configure.html')

        try:
            client = create_s3_client(access_key, secret_key, region,
                                      endpoint_url if endpoint_url else None)
            test_s3_connection(client)

            session['aws_configured'] = True
            session['aws_config'] = {
                'access_key': access_key,
                'secret_key': secret_key,
                'region': region,
                'endpoint_url': endpoint_url,
            }

            flash('AWS 配置成功！', 'success')
            return redirect(url_for('buckets'))

        except Exception as e:
            session.pop('aws_configured', None)
            session.pop('aws_config', None)
            flash(f'配置失败: {str(e)}', 'error')
            return render_template('configure.html')

    return render_template('configure.html')


@app.route('/buckets')
def buckets():
    """显示 S3 存储桶列表 - 支持分页"""
    if not session.get('aws_configured'):
        return redirect(url_for('configure'))

    s3 = get_s3_client()
    if s3 is None:
        flash('AWS 客户端未初始化，请重新配置', 'error')
        return redirect(url_for('configure'))

    try:
        response = s3.list_buckets()
        buckets_list = response.get('Buckets', [])

        sort_by = request.args.get('sort', 'name')
        sort_order = request.args.get('order', 'asc')
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))

        if sort_by == 'name':
            buckets_list = sorted(buckets_list, key=lambda x: x['Name'].lower(),
                                  reverse=(sort_order == 'desc'))
        elif sort_by == 'date':
            buckets_list = sorted(buckets_list, key=lambda x: x['CreationDate'],
                                  reverse=(sort_order == 'desc'))

        total_buckets = len(buckets_list)
        total_pages = (total_buckets + per_page - 1) // per_page if total_buckets > 0 else 1
        start_idx = (page - 1) * per_page
        buckets_page = buckets_list[start_idx:start_idx + per_page]

        return render_template('buckets.html',
                               buckets=buckets_page,
                               total_buckets=total_buckets,
                               current_page=page,
                               total_pages=total_pages,
                               per_page=per_page,
                               sort_by=sort_by,
                               sort_order=sort_order)
    except Exception as e:
        flash(f'获取存储桶列表失败: {str(e)}', 'error')
        return redirect(url_for('configure'))


@app.route('/bucket/<bucket_name>')
def bucket_contents(bucket_name):
    """显示存储桶内容 - 支持分页"""
    if not session.get('aws_configured'):
        return redirect(url_for('configure'))

    s3 = get_s3_client()
    if s3 is None:
        flash('AWS 客户端未初始化，请重新配置', 'error')
        return redirect(url_for('configure'))

    prefix = request.args.get('prefix', '')
    sort_by = request.args.get('sort', 'date')
    sort_order = request.args.get('order', 'desc')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 100))

    try:
        paginator = s3.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=prefix, Delimiter='/')

        folders, files = [], []
        for page_obj in page_iterator:
            for folder in page_obj.get('CommonPrefixes', []):
                folder_name = folder['Prefix']
                folders.append({
                    'name': folder_name.rstrip('/').split('/')[-1],
                    'path': folder_name,
                })
            for obj in page_obj.get('Contents', []):
                if obj['Key'] != prefix:
                    local_time = convert_to_local_time(obj['LastModified'])
                    files.append({
                        'name': obj['Key'].split('/')[-1],
                        'key': obj['Key'],
                        'size': obj['Size'],
                        'last_modified': local_time,
                        'last_modified_iso': local_time.isoformat(),
                        'etag': obj.get('ETag', ''),
                    })

        folders = sort_items(folders, 'name', 'asc', 'folder')
        files = sort_items(files, sort_by, sort_order, 'file')

        total_files = len(files)
        total_pages = (total_files + per_page - 1) // per_page
        start_idx = (page - 1) * per_page
        files_page = files[start_idx:start_idx + per_page]

        breadcrumbs = []
        if prefix:
            current_path = ''
            for part in prefix.rstrip('/').split('/'):
                current_path += part + '/'
                breadcrumbs.append({'name': part, 'path': current_path})

        return render_template('bucket_contents.html',
                               bucket_name=bucket_name,
                               folders=folders,
                               files=files_page,
                               total_files=total_files,
                               current_page=page,
                               total_pages=total_pages,
                               per_page=per_page,
                               current_prefix=prefix,
                               breadcrumbs=breadcrumbs,
                               sort_by=sort_by,
                               sort_order=sort_order)
    except Exception as e:
        flash(f'获取存储桶内容失败: {str(e)}', 'error')
        return redirect(url_for('buckets'))


@app.route('/api/bucket/<bucket_name>/contents')
def api_bucket_contents(bucket_name):
    """API: 获取存储桶内容 - 用于Ajax加载"""
    if not session.get('aws_configured'):
        return jsonify({'error': '未配置AWS凭证'}), 401

    s3 = get_s3_client()
    if s3 is None:
        return jsonify({'error': 'AWS 客户端未初始化'}), 500

    prefix = request.args.get('prefix', '')
    sort_by = request.args.get('sort', 'date')
    sort_order = request.args.get('order', 'desc')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))

    try:
        paginator = s3.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=prefix, Delimiter='/')

        folders, files = [], []
        for page_obj in page_iterator:
            for folder in page_obj.get('CommonPrefixes', []):
                folder_path = folder['Prefix']
                folders.append({
                    'name': folder_path.rstrip('/').split('/')[-1],
                    'path': folder_path,
                })
            for obj in page_obj.get('Contents', []):
                if obj['Key'] != prefix:
                    local_time = convert_to_local_time(obj['LastModified'])
                    files.append({
                        'name': obj['Key'].split('/')[-1],
                        'key': obj['Key'],
                        'size': obj['Size'],
                        'size_formatted': format_file_size(obj['Size']),
                        'last_modified': local_time.isoformat(),
                        'etag': obj.get('ETag', ''),
                    })

        folders = sort_items(folders, 'name', 'asc', 'folder')
        files = sort_items(files, sort_by, sort_order, 'file')

        total_files = len(files)
        total_pages = (total_files + per_page - 1) // per_page if total_files > 0 else 1
        start_idx = (page - 1) * per_page
        files_page = files[start_idx:start_idx + per_page]

        return jsonify({
            'folders': folders,
            'files': files_page,
            'total_files': total_files,
            'total_folders': len(folders),
            'current_page': page,
            'total_pages': total_pages,
            'has_more': page < total_pages,
        })
    except Exception as e:
        return jsonify({'error': f'获取内容失败: {str(e)}'}), 500


@app.route('/download/<bucket_name>/<path:object_key>')
def download_object(bucket_name, object_key):
    """生成预签名下载 URL"""
    if not session.get('aws_configured'):
        return redirect(url_for('configure'))

    s3 = get_s3_client()
    if s3 is None:
        flash('AWS 客户端未初始化，请重新配置', 'error')
        return redirect(url_for('configure'))

    try:
        url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': object_key},
            ExpiresIn=3600,
        )
        return redirect(url)
    except Exception as e:
        flash(f'生成下载链接失败: {str(e)}', 'error')
        return redirect(url_for('bucket_contents', bucket_name=bucket_name))


@app.route('/search')
def search_page():
    """搜索页面"""
    if not session.get('aws_configured'):
        return redirect(url_for('configure'))

    s3 = get_s3_client()
    if s3 is None:
        flash('AWS 客户端未初始化，请重新配置', 'error')
        return redirect(url_for('configure'))

    try:
        response = s3.list_buckets()
        return render_template('search.html', buckets=response.get('Buckets', []))
    except Exception as e:
        flash(f'获取存储桶列表失败: {str(e)}', 'error')
        return redirect(url_for('configure'))


@app.route('/api/session-status')
def api_session_status():
    """API: 检查会话状态"""
    return jsonify({
        'aws_configured': session.get('aws_configured', False),
        's3_client_initialized': get_s3_client() is not None,
        'session_id': session.get('session_id', 'unknown'),
    })


@app.route('/api/folders/<bucket_name>')
def api_get_folders(bucket_name):
    """API: 获取存储桶中的文件夹列表"""
    if not session.get('aws_configured'):
        return jsonify({'error': '未配置AWS凭证'}), 401

    s3 = get_s3_client()
    if s3 is None:
        return jsonify({'error': 'AWS 客户端未初始化'}), 500

    prefix = request.args.get('prefix', '')

    try:
        paginator = s3.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=prefix,
                                           Delimiter='/', MaxKeys=1000)
        folders = []
        for page in page_iterator:
            for folder in page.get('CommonPrefixes', []):
                folder_path = folder['Prefix']
                folders.append({
                    'name': folder_path.rstrip('/').split('/')[-1],
                    'path': folder_path,
                })

        folders = sort_items(folders, 'name', 'asc', 'folder')
        return jsonify({'folders': folders, 'total': len(folders)})
    except Exception as e:
        return jsonify({'error': f'获取文件夹列表失败: {str(e)}'}), 500


@app.route('/api/search/<bucket_name>')
def api_search_bucket(bucket_name):
    """API: 搜索存储桶内容 - 支持分页"""
    if not session.get('aws_configured'):
        return jsonify({'error': '未配置AWS凭证'}), 401

    s3 = get_s3_client()
    if s3 is None:
        return jsonify({'error': 'AWS 客户端未初始化'}), 500

    query = request.args.get('q', '').strip()
    prefix = request.args.get('prefix', '')
    sort_by = request.args.get('sort', 'date')
    sort_order = request.args.get('order', 'desc')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))

    if not query:
        return jsonify({'error': '搜索词不能为空'}), 400
    if len(query) < 2:
        return jsonify({'error': '搜索词至少需要2个字符'}), 400
    if len(query) > 100:
        return jsonify({'error': '搜索词不能超过100个字符'}), 400
    if not bucket_name or len(bucket_name) > 63:
        return jsonify({'error': '无效的存储桶名称'}), 400

    try:
        paginator = s3.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=prefix,
                                           Delimiter='/', MaxKeys=1000)

        folders, files = [], []
        folder_paths = set()
        total_count = 0
        max_results = 1000
        q = query.lower()

        for page_obj in page_iterator:
            for folder in page_obj.get('CommonPrefixes', []):
                if total_count >= max_results:
                    break
                folder_path = folder['Prefix']
                folder_name = folder_path.rstrip('/').split('/')[-1]
                if q in folder_name.lower() or q in folder_path.lower():
                    if folder_path not in folder_paths:
                        folders.append({'name': folder_name, 'path': folder_path})
                        folder_paths.add(folder_path)
                        total_count += 1

            for obj in page_obj.get('Contents', []):
                if total_count >= max_results:
                    break
                file_key = obj['Key']
                file_name = file_key.split('/')[-1]
                if q in file_name.lower() or q in file_key.lower():
                    local_time = convert_to_local_time(obj['LastModified'])
                    files.append({
                        'name': file_name,
                        'key': file_key,
                        'size': obj['Size'],
                        'size_formatted': format_file_size(obj['Size']),
                        'last_modified': local_time.isoformat(),
                        'etag': obj.get('ETag', ''),
                    })
                    total_count += 1

            if total_count >= max_results:
                break

        folders = sort_items(folders, 'name', 'asc', 'folder')
        files = sort_items(files, sort_by, sort_order, 'file')

        total_files = len(files)
        total_pages = (total_files + per_page - 1) // per_page if total_files > 0 else 1
        start_idx = (page - 1) * per_page
        files_page = files[start_idx:start_idx + per_page]

        return jsonify({
            'folders': folders if page == 1 else [],
            'files': files_page,
            'total': len(folders) + total_files,
            'total_files': total_files,
            'total_folders': len(folders),
            'current_page': page,
            'total_pages': total_pages,
            'has_more': page < total_pages or total_count >= max_results,
            'sort_by': sort_by,
            'sort_order': sort_order,
        })
    except Exception as e:
        return jsonify({'error': f'搜索失败: {str(e)}'}), 500


@app.route('/search/<bucket_name>')
def search_bucket(bucket_name):
    """搜索存储桶内容（旧版本，保留兼容性）"""
    if not session.get('aws_configured'):
        return redirect(url_for('configure'))

    s3 = get_s3_client()
    if s3 is None:
        flash('AWS 客户端未初始化，请重新配置', 'error')
        return redirect(url_for('configure'))

    query = request.args.get('q', '').strip()
    sort_by = request.args.get('sort', 'date')
    sort_order = request.args.get('order', 'desc')

    if not query:
        return redirect(url_for('bucket_contents', bucket_name=bucket_name))

    try:
        paginator = s3.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=query)

        results = []
        for page in page_iterator:
            for obj in page.get('Contents', []):
                if query.lower() in obj['Key'].lower():
                    results.append({
                        'name': obj['Key'].split('/')[-1],
                        'key': obj['Key'],
                        'size': obj['Size'],
                        'last_modified': convert_to_local_time(obj['LastModified']),
                        'etag': obj.get('ETag', ''),
                    })

        results = sort_items(results, sort_by, sort_order, 'file')
        return render_template('search_results.html',
                               bucket_name=bucket_name,
                               query=query,
                               results=results,
                               sort_by=sort_by,
                               sort_order=sort_order)
    except Exception as e:
        flash(f'搜索失败: {str(e)}', 'error')
        return redirect(url_for('bucket_contents', bucket_name=bucket_name))


@app.route('/logout')
def logout():
    """清除配置并退出"""
    session.clear()
    flash('已退出，配置已清除', 'info')
    return redirect(url_for('configure'))


@app.errorhandler(404)
def not_found_error(error):
    flash('页面未找到', 'error')
    return redirect(url_for('index'))


@app.errorhandler(500)
def internal_error(error):
    flash('服务器内部错误', 'error')
    return redirect(url_for('index'))


# 本地调试：python api/index.py
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    app.run(host="0.0.0.0", port=port, debug=False)
