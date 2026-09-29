#include "../common/asterbench_v11_record.h"

#include <iostream>
#include <string>

int main() {
    std::string length;
    while (std::getline(std::cin, length)) {
        unsigned count;
        try {
            count = std::stoul(length);
            if (count > 100000) return 2;
        } catch (...) {
            return 2;
        }
        std::string line(count, '\0');
        std::cin.read(line.data(), count);
        if (static_cast<unsigned>(std::cin.gcount()) != count) return 2;
        try {
            validate_asterbench_v11_record(line);
            std::cout << "PASS\n";
        } catch (const std::exception&) {
            std::cout << "FAIL\n";
        }
    }
}
