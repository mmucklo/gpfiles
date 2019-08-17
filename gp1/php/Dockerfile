FROM php:7.3-fpm
RUN apt update
RUN apt install -y git vim unzip sqlite3
RUN apt-get update && apt-get install -y \
        libfreetype6-dev \
        libjpeg62-turbo-dev \
        libpng-dev \
        libicu-dev \
	libicu63 \
        mysql-common \
        libpq-dev \
	libxml2-dev \
        libfreetype6-dev \
        libedit2 libedit-dev \
        libgmp-dev \
	zlib1g-dev \
	bzip2 \
        libbz2-dev \
        libkrb5-dev \
        libc-client-dev \
        libldap2-dev \
	freetds-dev \
        libsybdb5 \
        libpspell-dev \
        librecode-dev \
        libsnmp-dev \
        libtidy-dev \
        libxslt1-dev \
        libzip-dev \
    && docker-php-ext-install -j$(nproc) bcmath bz2 calendar dba exif gettext gmp intl ldap mysqli opcache pcntl pdo_mysql pdo_pgsql pgsql pspell recode shmop simplexml snmp soap sockets sysvmsg sysvsem sysvshm tidy wddx xsl zip \
    && docker-php-ext-configure imap --with-imap-ssl --with-kerberos \
    && docker-php-ext-install imap \
    && docker-php-ext-configure gd --with-freetype-dir=/usr/include/ --with-jpeg-dir=/usr/include/ \
    && docker-php-ext-install gd
RUN apt-get update && apt-get install -y libmemcached-dev zlib1g-dev \
    && pecl install memcached \
    && docker-php-ext-enable memcached
RUN pecl install redis \
    && pecl install xdebug \
    && docker-php-ext-enable redis \
    && docker-php-ext-enable xdebug
RUN curl --url https://dl.google.com/go/go1.12.7.linux-amd64.tar.gz -o /golang.tgz && cd /usr/local && tar xzf /golang.tgz && rm -f /golang.tgz
RUN mkdir /caddy
COPY caddy.go /caddy/.
ARG GO111MODULE=on
RUN cd /caddy/ && /usr/local/go/bin/go mod init caddy && /usr/local/go/bin/go mod tidy -v && /usr/local/go/bin/go build . && mv ./caddy /usr/local/bin/. && cd / && rm -rf caddy
COPY zz-docker.conf /usr/local/etc/php-fpm.d/zz-docker.conf
RUN mkdir /var/run/php
RUN php -r "copy('https://getcomposer.org/installer', 'composer-setup.php');"
RUN php -r "if (hash_file('sha384', 'composer-setup.php') === 'a5c698ffe4b8e849a443b120cd5ba38043260d5c4023dbf93e1558871f1f07f58274fc6f4c93bcfd858c6bd0775cd8d1') { echo 'Installer verified'; } else { echo 'Installer corrupt'; unlink('composer-setup.php'); } echo PHP_EOL;"
RUN php composer-setup.php
RUN php -r "unlink('composer-setup.php');"
RUN mv composer.phar /usr/local/bin/composer
RUN apt install -y wget
RUN wget https://get.symfony.com/cli/installer -O - | bash
RUN mv /root/.symfony/bin/symfony /usr/local/bin/symfony
COPY entrypoint.sh /usr/local/bin/
ENTRYPOINT /usr/local/bin/entrypoint.sh