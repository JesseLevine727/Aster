// Length-framed host mutation corpus; never executes firmware.
#include "../common/dma_record.h"
#include <iostream>
int main() {
    std::string size;
    while (std::getline(std::cin,size)) {
        unsigned count;
        try { count = std::stoul(size); if (count > 100000) return 2; }
        catch (...) { return 2; }
        std::string line(count,'\0'); std::cin.read(&line[0],count);
        if (unsigned(std::cin.gcount()) != count) return 2;
        try { validate_dma_record(line); std::cout << "PASS\n"; }
        catch (const std::exception&) { std::cout << "FAIL\n"; }
    }
}
